"""Administrator product management: the tracking form, versions and lifecycle."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import delete, func, select

from app.core.deps import DbSession, client_context, require_permissions
from app.core.errors import unprocessable
from app.core.pagination import Page, PageParamsDep, build_page
from app.core.permissions import Permission
from app.models.admin import Admin
from app.models.enums import ProductStatus
from app.models.farming import Planting
from app.models.product import (
    Product,
    ProductBenefit,
    ProductNutrient,
    ProductScheduleTask,
)
from app.schemas.farming import (
    ProductDetail,
    ProductRead,
    ProductStatusRow,
    ProductWrite,
    TrackableBlocker,
    TrackableResponse,
)
from app.services import audit, product_service

router = APIRouter(prefix="/admin/products", tags=["admin.products"])

ReadProducts = Annotated[Admin, Depends(require_permissions(Permission.PRODUCTS_READ))]
WriteProducts = Annotated[Admin, Depends(require_permissions(Permission.PRODUCTS_WRITE))]

SORTABLE = {
    "name": Product.name_en,
    "status": Product.status,
    "version": Product.version,
    "created_at": Product.created_at,
    "updated_at": Product.updated_at,
}


def _apply_children(db, product: Product, payload: ProductWrite) -> None:
    """Replace the child rows that were sent. Omitted lists are left untouched."""
    if payload.benefits is not None:
        db.execute(delete(ProductBenefit).where(ProductBenefit.product_id == product.id))
        for benefit in payload.benefits:
            db.add(
                ProductBenefit(
                    product_id=product.id,
                    age_group=benefit.age_group,
                    benefit_text=benefit.benefit_text,
                )
            )

    if payload.nutrients is not None:
        db.execute(delete(ProductNutrient).where(ProductNutrient.product_id == product.id))
        for nutrient in payload.nutrients:
            db.add(
                ProductNutrient(
                    product_id=product.id,
                    nutrient=nutrient.nutrient.lower().replace(" ", "_"),
                    amount_per_100g=nutrient.amount_per_100g,
                    unit=nutrient.unit,
                    source=nutrient.source,
                )
            )

    if payload.schedule_tasks is not None:
        for task in payload.schedule_tasks:
            product_service.validate_schedule_task(task.model_dump())
        db.execute(delete(ProductScheduleTask).where(ProductScheduleTask.product_id == product.id))
        for task in payload.schedule_tasks:
            db.add(
                ProductScheduleTask(
                    product_id=product.id,
                    task_type=task.task_type,
                    day_offset=task.day_offset,
                    repeat_rule=task.repeat_rule,
                    repeat_weekday=task.repeat_weekday,
                    repeat_interval_days=task.repeat_interval_days,
                    repeat_until_day=task.repeat_until_day,
                    time_of_day=task.time_of_day,
                    instruction_text=task.instruction_text,
                    reminder_enabled=task.reminder_enabled,
                )
            )
    db.flush()


SCALAR_FIELDS = (
    "food_item_id",
    "name_en",
    "name_rw",
    "photo_url",
    "description_en",
    "description_rw",
    "how_not_to_use",
    "how_to_preserve",
    "conditions_diseases",
    "reviewer_name",
    "reviewer_organisation",
    "review_date",
    "time_to_harvest_min_days",
    "time_to_harvest_max_days",
    "harvest_method",
    "irrigation_method",
    "irrigation_frequency",
    "irrigation_time_of_day",
    "place_type",
    "temperature_min_c",
    "temperature_max_c",
    "humidity_min_pct",
    "humidity_max_pct",
    "resources",
    "recipes",
    "season_suitability",
    "spray_details",
    "needs_expert_review",
    "growing_steps_validated",
)


def _apply_scalars(product: Product, payload: ProductWrite) -> None:
    for field in SCALAR_FIELDS:
        value = getattr(payload, field)
        if field in payload.model_fields_set:
            if field in {"resources", "recipes"} and value is not None:
                setattr(product, field, [item.model_dump() for item in value])
            else:
                setattr(product, field, value)


@router.get("", response_model=Page[ProductRead], summary="List products")
def list_products(
    params: PageParamsDep,
    db: DbSession,
    admin: ReadProducts,
    status_filter: Annotated[ProductStatus | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query(max_length=160)] = None,
) -> dict:
    query = select(Product)
    if status_filter is not None:
        query = query.where(Product.status == status_filter)
    if q:
        pattern = f"%{q.strip().lower()}%"
        query = query.where(func.lower(Product.name_en).like(pattern))

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    column = SORTABLE.get(params.sort or "updated_at", Product.updated_at)
    ordering = column.asc() if params.order == "asc" else column.desc()
    rows = db.scalars(query.order_by(ordering).offset(params.offset).limit(params.page_size))
    return build_page([ProductRead.model_validate(row) for row in rows], total, params)


@router.get(
    "/status-table",
    response_model=Page[ProductStatusRow],
    summary="Status table with blockers and how many farmers are planting",
)
def status_table(params: PageParamsDep, db: DbSession, admin: ReadProducts) -> dict:
    rows = db.scalars(select(Product).order_by(Product.name_en)).all()
    counts = dict(
        db.execute(select(Planting.product_id, func.count()).group_by(Planting.product_id)).all()
    )

    items: list[ProductStatusRow] = []
    for product in rows:
        blockers = [
            TrackableBlocker(**blocker) for blocker in product_service.trackable_blockers(product)
        ]
        items.append(
            ProductStatusRow(
                id=product.id,
                name_en=product.name_en,
                status=product.status,
                version=product.version,
                reviewer_name=product.reviewer_name,
                review_date=product.review_date,
                farmers_planting=int(counts.get(product.id, 0)),
                last_edited_at=product.updated_at,
                blockers=blockers,
            )
        )

    total = len(items)
    start = params.offset
    page = items[start : start + params.page_size]
    return build_page(page, total, params)


@router.post("", response_model=ProductDetail, status_code=201, summary="Create a draft product")
def create_product(
    payload: ProductWrite,
    db: DbSession,
    admin: WriteProducts,
    context: Annotated[dict, Depends(client_context)],
) -> ProductDetail:
    if not payload.name_en:
        raise unprocessable(
            "product.missing.name_en",
            "errors.products.missing.name_en",
            "The English name is required",
        )

    product = Product(
        name_en=payload.name_en,
        status=ProductStatus.DRAFT,
        needs_expert_review=payload.needs_expert_review
        if payload.needs_expert_review is not None
        else True,
    )
    db.add(product)
    db.flush()
    _apply_scalars(product, payload)
    _apply_children(db, product, payload)

    audit.record(
        db,
        action="product.create",
        entity_type="product",
        actor_id=admin.id,
        actor_label=admin.email or admin.name,
        entity_id=product.id,
        payload={"name_en": product.name_en},
        ip_address=context["ip_address"],
        user_agent=context["user_agent"],
    )
    db.commit()
    db.refresh(product)
    return ProductDetail.model_validate(product)


@router.get("/{product_id}", response_model=ProductDetail, summary="Read one product")
def get_product(product_id: uuid.UUID, db: DbSession, admin: ReadProducts) -> ProductDetail:
    return ProductDetail.model_validate(product_service.get_product(db, product_id))


@router.get(
    "/{product_id}/preview",
    response_model=ProductDetail,
    summary="Preview exactly what a farmer would see",
)
def preview_product(product_id: uuid.UUID, db: DbSession, admin: ReadProducts) -> ProductDetail:
    return ProductDetail.model_validate(product_service.get_product(db, product_id))


@router.put("/{product_id}", response_model=ProductDetail, summary="Update a product")
def update_product(
    product_id: uuid.UUID,
    payload: ProductWrite,
    db: DbSession,
    admin: WriteProducts,
    context: Annotated[dict, Depends(client_context)],
) -> ProductDetail:
    product = product_service.get_product(db, product_id)
    was_trackable = product.status is ProductStatus.TRACKABLE
    if was_trackable:
        # Archive the state being replaced before anything changes.
        product_service.record_version(db, product, admin.id)

    _apply_scalars(product, payload)
    _apply_children(db, product, payload)
    db.flush()

    if was_trackable:
        # Changing a live product creates a new version. Plantings keep theirs.
        product_service.bump_version(db, product, admin.id)

    audit.record(
        db,
        action="product.update",
        entity_type="product",
        actor_id=admin.id,
        actor_label=admin.email or admin.name,
        entity_id=product.id,
        payload={"fields": sorted(payload.model_fields_set), "version": product.version},
        ip_address=context["ip_address"],
        user_agent=context["user_agent"],
    )
    db.commit()
    db.refresh(product)
    return ProductDetail.model_validate(product)


@router.post(
    "/{product_id}/mark-trackable",
    response_model=TrackableResponse,
    summary="Publish a product so farmers can plant it",
)
def mark_trackable(
    product_id: uuid.UUID,
    db: DbSession,
    admin: WriteProducts,
    context: Annotated[dict, Depends(client_context)],
) -> TrackableResponse:
    product = product_service.get_product(db, product_id)
    product = product_service.mark_trackable(db, product, admin.id)

    audit.record(
        db,
        action="product.mark_trackable",
        entity_type="product",
        actor_id=admin.id,
        actor_label=admin.email or admin.name,
        entity_id=product.id,
        payload={"version": product.version},
        ip_address=context["ip_address"],
        user_agent=context["user_agent"],
    )
    db.commit()
    db.refresh(product)
    return TrackableResponse(product=ProductRead.model_validate(product))


@router.post("/{product_id}/pause", response_model=ProductRead, summary="Pause a product")
def pause_product(
    product_id: uuid.UUID,
    db: DbSession,
    admin: WriteProducts,
    context: Annotated[dict, Depends(client_context)],
) -> ProductRead:
    product = product_service.get_product(db, product_id)
    product = product_service.pause(db, product, admin.id)
    audit.record(
        db,
        action="product.pause",
        entity_type="product",
        actor_id=admin.id,
        actor_label=admin.email or admin.name,
        entity_id=product.id,
        payload={},
        ip_address=context["ip_address"],
        user_agent=context["user_agent"],
    )
    db.commit()
    return ProductRead.model_validate(product)


@router.post("/{product_id}/retire", response_model=ProductRead, summary="Retire a product")
def retire_product(
    product_id: uuid.UUID,
    db: DbSession,
    admin: WriteProducts,
    context: Annotated[dict, Depends(client_context)],
) -> ProductRead:
    product = product_service.get_product(db, product_id)
    product = product_service.retire(db, product, admin.id)
    audit.record(
        db,
        action="product.retire",
        entity_type="product",
        actor_id=admin.id,
        actor_label=admin.email or admin.name,
        entity_id=product.id,
        payload={},
        ip_address=context["ip_address"],
        user_agent=context["user_agent"],
    )
    db.commit()
    return ProductRead.model_validate(product)


@router.get(
    "/{product_id}/blockers",
    response_model=list[TrackableBlocker],
    summary="Why a product cannot become trackable",
)
def blockers(product_id: uuid.UUID, db: DbSession, admin: ReadProducts) -> list[TrackableBlocker]:
    product = product_service.get_product(db, product_id)
    return [TrackableBlocker(**blocker) for blocker in product_service.trackable_blockers(product)]
