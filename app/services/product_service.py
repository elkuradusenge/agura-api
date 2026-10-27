"""Product lifecycle: the trackable rule, versioning and farmer visibility.

The rule that keeps the platform honest: a product may only become trackable when
every required field is filled, it has at least one schedule task, and a named
reviewer with a review date has signed off. Farmers only ever see trackable products.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import not_found, unprocessable
from app.models.enums import AGE_GROUPS, ProductStatus, RepeatRule, TaskType
from app.models.product import Product, ProductVersion
from app.ws import broadcaster

REQUIRED_TEXT_FIELDS = (
    ("name_en", "products.missing.name_en"),
    ("name_rw", "products.missing.name_rw"),
    ("photo_url", "products.missing.photo"),
    ("description_en", "products.missing.description"),
    ("how_not_to_use", "products.missing.how_not_to_use"),
    ("how_to_preserve", "products.missing.how_to_preserve"),
    ("irrigation_method", "products.missing.irrigation_method"),
    ("harvest_method", "products.missing.harvest_method"),
    ("place_type", "products.missing.place"),
    ("conditions_diseases", "products.missing.conditions"),
)

REQUIRED_NUMERIC_FIELDS = (
    ("time_to_harvest_min_days", "products.missing.harvest_days"),
    ("time_to_harvest_max_days", "products.missing.harvest_days"),
)


def _missing_fields(product: Product) -> list[dict[str, Any]]:
    missing: list[dict[str, Any]] = []
    for field, translation_key in REQUIRED_TEXT_FIELDS:
        value = getattr(product, field)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append({"field": field, "translation_key": translation_key})

    for field, translation_key in REQUIRED_NUMERIC_FIELDS:
        if getattr(product, field) is None:
            missing.append({"field": field, "translation_key": translation_key})

    if product.irrigation_frequency is None:
        missing.append(
            {"field": "irrigation_frequency", "translation_key": "products.missing.irrigation"}
        )
    if product.irrigation_time_of_day is None:
        missing.append(
            {"field": "irrigation_time_of_day", "translation_key": "products.missing.irrigation"}
        )

    benefits = {benefit.age_group for benefit in product.benefits if benefit.benefit_text}
    for group in AGE_GROUPS:
        if group not in benefits:
            missing.append(
                {
                    "field": f"benefits.{group.value}",
                    "translation_key": "products.missing.benefit",
                }
            )

    if not any(nutrient.amount_per_100g is not None for nutrient in product.nutrients):
        missing.append({"field": "nutrients", "translation_key": "products.missing.nutrients"})

    if len([recipe for recipe in (product.recipes or []) if recipe.get("name")]) < 2:
        missing.append({"field": "recipes", "translation_key": "products.missing.recipes"})

    if not product.reviewer_name or not product.reviewer_organisation:
        missing.append({"field": "reviewer", "translation_key": "products.missing.reviewer"})
    if product.review_date is None:
        missing.append({"field": "review_date", "translation_key": "products.missing.review_date"})

    if not [task for task in product.schedule_tasks]:
        missing.append({"field": "schedule_tasks", "translation_key": "products.missing.schedule"})

    return missing


def trackable_blockers(product: Product) -> list[dict[str, Any]]:
    """Everything standing between this product and being trackable."""
    return _missing_fields(product)


def can_be_trackable(product: Product) -> bool:
    return not trackable_blockers(product)


def snapshot(product: Product) -> dict[str, Any]:
    """A full, restorable copy of a product and everything under it."""
    return {
        "name_en": product.name_en,
        "name_rw": product.name_rw,
        "photo_url": product.photo_url,
        "description_en": product.description_en,
        "description_rw": product.description_rw,
        "how_not_to_use": product.how_not_to_use,
        "how_to_preserve": product.how_to_preserve,
        "conditions_diseases": product.conditions_diseases,
        "reviewer_name": product.reviewer_name,
        "reviewer_organisation": product.reviewer_organisation,
        "review_date": product.review_date.isoformat() if product.review_date else None,
        "time_to_harvest_min_days": product.time_to_harvest_min_days,
        "time_to_harvest_max_days": product.time_to_harvest_max_days,
        "harvest_method": product.harvest_method,
        "irrigation_method": product.irrigation_method,
        "irrigation_frequency": (
            product.irrigation_frequency.value if product.irrigation_frequency else None
        ),
        "irrigation_time_of_day": (
            product.irrigation_time_of_day.value if product.irrigation_time_of_day else None
        ),
        "place_type": product.place_type,
        "resources": product.resources,
        "recipes": product.recipes,
        "season_suitability": product.season_suitability,
        "spray_details": product.spray_details,
        "benefits": [
            {"age_group": benefit.age_group.value, "benefit_text": benefit.benefit_text}
            for benefit in product.benefits
        ],
        "nutrients": [
            {
                "nutrient": nutrient.nutrient,
                "amount_per_100g": (
                    float(nutrient.amount_per_100g)
                    if nutrient.amount_per_100g is not None
                    else None
                ),
                "unit": nutrient.unit,
                "source": nutrient.source,
            }
            for nutrient in product.nutrients
        ],
        "schedule_tasks": [
            {
                "task_type": task.task_type.value,
                "day_offset": task.day_offset,
                "repeat_rule": task.repeat_rule.value,
                "repeat_weekday": task.repeat_weekday,
                "repeat_interval_days": task.repeat_interval_days,
                "repeat_until_day": task.repeat_until_day,
                "time_of_day": task.time_of_day.value,
                "instruction_text": task.instruction_text,
                "reminder_enabled": task.reminder_enabled,
            }
            for task in product.schedule_tasks
        ],
    }


def record_version(db: Session, product: Product, admin_id: uuid.UUID | None) -> ProductVersion:
    """Archive the current state at the current version.

    Idempotent: version numbers are a timeline, so the first snapshot for a version
    always wins and re-publishing never rewrites history.
    """
    existing = db.scalar(
        select(ProductVersion).where(
            ProductVersion.product_id == product.id,
            ProductVersion.version == product.version,
        )
    )
    if existing is not None:
        return existing

    version = ProductVersion(
        product_id=product.id,
        version=product.version,
        snapshot=snapshot(product),
        created_by_id=admin_id,
    )
    db.add(version)
    db.flush()
    return version


def bump_version(
    db: Session, product: Product, admin_id: uuid.UUID | None, *, notify: bool = True
) -> None:
    """Archive the state before a change, move to the next version, archive that too.

    Call this after the changes are applied and flushed, with `previous_version`
    telling it which version the pre-change snapshot belongs to.
    """
    record_version(db, product, admin_id)
    product.version += 1
    db.flush()
    record_version(db, product, admin_id)
    if notify and product.status is ProductStatus.TRACKABLE:
        broadcaster.notify_all_users(
            db,
            "product.updated",
            {
                "product_id": str(product.id),
                "name_en": product.name_en,
                "version": product.version,
            },
        )


def get_product(db: Session, product_id: uuid.UUID) -> Product:
    product = db.get(Product, product_id)
    if product is None:
        raise not_found("product")
    return product


def get_trackable_product(db: Session, product_id: uuid.UUID) -> Product:
    """Farmers only ever reach this path, so anything else is simply not found."""
    product = db.get(Product, product_id)
    if product is None or product.status is not ProductStatus.TRACKABLE:
        raise not_found("product")
    return product


def mark_trackable(db: Session, product: Product, admin_id: uuid.UUID | None) -> Product:
    blockers = trackable_blockers(product)
    if blockers:
        raise unprocessable(
            "product.not_ready",
            "errors.product.not_ready",
            "This product cannot become trackable yet",
            {"blockers": blockers},
        )

    if product.status is ProductStatus.TRACKABLE:
        return product

    if product.status is ProductStatus.RETIRED:
        raise unprocessable(
            "product.retired",
            "errors.product.retired",
            "A retired product cannot be made trackable again, create a new one",
        )

    product.status = ProductStatus.TRACKABLE
    product.published_at = datetime.now(UTC)
    product.paused_at = None
    db.flush()
    record_version(db, product, admin_id)
    db.commit()
    db.refresh(product)

    broadcaster.notify_all_users(
        db,
        "product.published",
        {
            "product_id": str(product.id),
            "name_en": product.name_en,
            "name_rw": product.name_rw,
            "version": product.version,
        },
    )
    return product


def pause(db: Session, product: Product, admin_id: uuid.UUID | None) -> Product:
    if product.status is not ProductStatus.TRACKABLE:
        raise unprocessable(
            "product.not_trackable",
            "errors.product.not_trackable",
            "Only a trackable product can be paused",
        )
    product.status = ProductStatus.PAUSED
    product.paused_at = datetime.now(UTC)
    record_version(db, product, admin_id)
    db.commit()
    db.refresh(product)
    return product


def retire(db: Session, product: Product, admin_id: uuid.UUID | None) -> Product:
    if product.status is ProductStatus.RETIRED:
        return product
    product.status = ProductStatus.RETIRED
    product.retired_at = datetime.now(UTC)
    record_version(db, product, admin_id)
    db.commit()
    db.refresh(product)
    return product


def farmervisible_products(db: Session) -> list[Product]:
    return list(
        db.scalars(
            select(Product)
            .where(Product.status == ProductStatus.TRACKABLE)
            .order_by(Product.name_en)
        )
    )


def validate_schedule_task(payload: dict[str, Any]) -> None:
    """Repeat rules are only meaningful with the right extra fields."""
    rule = payload.get("repeat_rule") or RepeatRule.ONCE
    rule = RepeatRule(rule) if not isinstance(rule, RepeatRule) else rule

    if rule is RepeatRule.WEEKLY and payload.get("repeat_weekday") is None:
        raise unprocessable(
            "product.schedule.weekday_required",
            "errors.product.schedule.weekday_required",
            "A weekly task needs a weekday",
        )
    if rule is RepeatRule.CUSTOM and not payload.get("repeat_interval_days"):
        raise unprocessable(
            "product.schedule.interval_required",
            "errors.product.schedule.interval_required",
            "A custom repeat needs an interval in days",
        )
    if not str(payload.get("instruction_text") or "").strip():
        raise unprocessable(
            "product.schedule.instruction_required",
            "errors.product.schedule.instruction_required",
            "Every task needs an instruction",
        )
    TaskType(payload.get("task_type") or TaskType.CHECK)
    int(payload.get("day_offset") or 0)
