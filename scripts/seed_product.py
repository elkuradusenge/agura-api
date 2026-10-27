"""Seed the first product: Oyster mushroom, as a draft.

Nutrition values and health statements are deliberately empty and flagged
"needs expert review". Growing steps are flagged "indicative, validate with RAB".
It must never become trackable automatically, so this only ever creates a draft.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import (
    AGE_GROUPS,
    IrrigationFrequency,
    ProductStatus,
    RepeatRule,
    TaskType,
    TimeOfDay,
)
from app.models.household import FoodItem
from app.models.product import (
    Product,
    ProductBenefit,
    ProductScheduleTask,
)

MUSHROOM_VARIABLE = "h25_21"
MUSHROOM_NAME_EN = "Oyster mushroom"
MUSHROOM_NAME_RW = "Ubunyobwa bw'igituba"

INDICATIVE_NOTE = "Indicative, validate with RAB"
EXPERT_NOTE = "Needs expert review"


def mushroom_draft(db: Session) -> Product | None:
    return db.scalar(select(Product).where(Product.name_en == MUSHROOM_NAME_EN))


def seed_mushroom_product(db: Session, *, overwrite: bool = False) -> Product:
    """Create the draft once. Re-seeding never publishes it."""
    existing = mushroom_draft(db)
    if existing is not None and not overwrite:
        return existing

    food_item = db.scalar(select(FoodItem).where(FoodItem.source_variable == MUSHROOM_VARIABLE))
    if food_item is None:
        food_item = FoodItem(
            standard_name="Mushroom", group="fungi", source_variable=MUSHROOM_VARIABLE
        )
        db.add(food_item)
        db.flush()

    product = existing or Product(name_en=MUSHROOM_NAME_EN)
    product.food_item_id = food_item.id
    product.name_rw = MUSHROOM_NAME_RW
    product.photo_url = None
    product.description_en = (
        "Oyster mushrooms grow on agricultural waste in a shaded, humid space. "
        "They are harvested in cycles over several weeks. " + INDICATIVE_NOTE + "."
    )
    product.description_rw = None
    product.status = ProductStatus.DRAFT
    product.needs_expert_review = True
    product.growing_steps_validated = False
    product.how_not_to_use = None
    product.how_to_preserve = (
        "Keep harvested mushrooms cool and use them within two days, or dry them "
        "in a ventilated shaded place. " + INDICATIVE_NOTE + "."
    )
    product.conditions_diseases = None
    product.reviewer_name = None
    product.reviewer_organisation = None
    product.review_date = None

    product.time_to_harvest_min_days = 21
    product.time_to_harvest_max_days = 30
    product.harvest_method = (
        "Twist the whole cluster from the substrate, then clear the cut surface. "
        + INDICATIVE_NOTE
        + "."
    )
    product.irrigation_method = (
        "Mist the growing space and keep the substrate moist without soaking it. "
        + INDICATIVE_NOTE
        + "."
    )
    product.irrigation_frequency = IrrigationFrequency.DAILY
    product.irrigation_time_of_day = TimeOfDay.MORNING
    product.place_type = "shaded_room"
    product.temperature_min_c = 20
    product.temperature_max_c = 28
    product.humidity_min_pct = 80
    product.humidity_max_pct = 90
    product.resources = [
        {"name": "Mushroom spawn", "quantity": "1 bag", "estimated_cost": 3000},
        {"name": "Substrate (straw or sawdust)", "quantity": "20 kg", "estimated_cost": 2000},
        {"name": "Growing bags", "quantity": "10", "estimated_cost": 1500},
        {"name": "Clean water", "quantity": "as needed", "estimated_cost": None},
    ]
    product.recipes = [
        {
            "name": "Mushroom and vegetables",
            "ingredients": ["Oyster mushrooms", "Onion", "Tomato", "Cooking oil"],
            "steps": "Clean and slice the mushrooms, then cook with onion and tomato. "
            + INDICATIVE_NOTE
            + ".",
        },
        {
            "name": "Mushroom soup",
            "ingredients": ["Oyster mushrooms", "Water", "Salt", "Onion"],
            "steps": "Simmer the mushrooms with onion, then season. " + INDICATIVE_NOTE + ".",
        },
    ]
    product.season_suitability = {}
    product.spray_details = {
        "product": None,
        "dose": None,
        "interval": None,
        "safety_note": "Set by an administrator after RAB review.",
    }
    db.add(product)
    db.flush()

    # Benefits start empty: they are health statements and need an expert.
    existing_benefits = {benefit.age_group for benefit in product.benefits}
    for group in AGE_GROUPS:
        if group not in existing_benefits:
            db.add(ProductBenefit(product_id=product.id, age_group=group, benefit_text=None))

    if not product.schedule_tasks:
        _schedule = [
            (
                TaskType.IRRIGATION,
                0,
                RepeatRule.DAILY,
                None,
                28,
                TimeOfDay.MORNING,
                "Mist the growing space. " + INDICATIVE_NOTE,
            ),
            (
                TaskType.CHECK,
                7,
                RepeatRule.WEEKLY,
                2,
                28,
                TimeOfDay.MORNING,
                "Check for contamination and remove affected bags. " + INDICATIVE_NOTE,
            ),
            (
                TaskType.SPRAY_OR_TREATMENT,
                10,
                RepeatRule.ONCE,
                None,
                None,
                TimeOfDay.EVENING,
                "Apply the treatment the administrator sets. " + INDICATIVE_NOTE,
            ),
            (
                TaskType.HARVEST,
                21,
                RepeatRule.CUSTOM,
                None,
                42,
                TimeOfDay.MORNING,
                "Harvest mature clusters. " + INDICATIVE_NOTE,
            ),
        ]
        for (
            task_type,
            day_offset,
            repeat_rule,
            weekday,
            until,
            time_of_day,
            text,
        ) in _schedule:
            db.add(
                ProductScheduleTask(
                    product_id=product.id,
                    task_type=task_type,
                    day_offset=day_offset,
                    repeat_rule=repeat_rule,
                    repeat_weekday=weekday,
                    repeat_interval_days=7 if repeat_rule is RepeatRule.CUSTOM else None,
                    repeat_until_day=until,
                    time_of_day=time_of_day,
                    instruction_text=text,
                    reminder_enabled=True,
                )
            )

    db.commit()
    db.refresh(product)
    return product


__all__ = ["EXPERT_NOTE", "INDICATIVE_NOTE", "mushroom_draft", "seed_mushroom_product"]
