"""Seed one fully complete demo product so the farmer flow can be walked locally.

This is demo data, not content. Every health statement is a placeholder that
names itself as a placeholder, and the reviewer is "Demo seed". It exists so a
developer or a judge can plant something, get reminders, and record a harvest
without first inventing a month of agronomy content.

The real Oyster mushroom product stays a draft, exactly as `seed_product.py`
leaves it. Run this only when you want a product a farmer can actually plant:

    python -m scripts.seed_demo_product
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import (
    AGE_GROUPS,
    IrrigationFrequency,
    RepeatRule,
    TaskType,
    TimeOfDay,
)
from app.models.product import (
    Product,
    ProductBenefit,
    ProductScheduleTask,
)
from app.services import product_service

NAME_EN = "Demo: Orange-fleshed sweet potato"
NAME_RW = "Igitoki cy'umuhondo (icyitegererezo)"

DEMO_NOTE = "Demo placeholder, replace before any farmer sees it"

RESOURCES = [
    {"name": "Vine cuttings", "quantity": "200", "estimated_cost": 4000},
    {"name": "Compost", "quantity": "2 wheelbarrows", "estimated_cost": 3000},
    {"name": "Watering can", "quantity": "1", "estimated_cost": 5000},
]

RECIPES = [
    {
        "name": "Boiled sweet potato",
        "ingredients": ["Sweet potato", "Water"],
        "steps": "Wash, boil until soft, serve. " + DEMO_NOTE + ".",
    },
    {
        "name": "Sweet potato and greens",
        "ingredients": ["Sweet potato", "Leafy greens", "Onion", "Oil"],
        "steps": "Boil the potato, then fold in the greens. " + DEMO_NOTE + ".",
    },
]

SCHEDULE = [
    (
        TaskType.IRRIGATION,
        0,
        RepeatRule.DAILY,
        None,
        90,
        TimeOfDay.MORNING,
        "Water the bed, keep the soil moist. " + DEMO_NOTE,
    ),
    (
        TaskType.IRRIGATION,
        0,
        RepeatRule.DAILY,
        None,
        90,
        TimeOfDay.EVENING,
        "Check soil moisture before the evening water. " + DEMO_NOTE,
    ),
    (
        TaskType.CHECK,
        14,
        RepeatRule.WEEKLY,
        2,
        90,
        TimeOfDay.MORNING,
        "Check leaves for weevils and disease. " + DEMO_NOTE,
    ),
    (
        TaskType.SPRAY_OR_TREATMENT,
        21,
        RepeatRule.ONCE,
        None,
        None,
        TimeOfDay.EVENING,
        "Apply the treatment the administrator sets. " + DEMO_NOTE,
    ),
    (
        TaskType.HARVEST,
        90,
        RepeatRule.ONCE,
        None,
        None,
        TimeOfDay.MORNING,
        "Dig up the roots when the leaves yellow. " + DEMO_NOTE,
    ),
]


def demo_product(db: Session) -> Product | None:
    return db.scalar(select(Product).where(Product.name_en == NAME_EN))


def seed_demo_product(db: Session) -> Product:
    """Create or refresh the demo product, then publish it through the real rule."""
    product = demo_product(db) or Product(name_en=NAME_EN)

    product.name_rw = NAME_RW
    product.photo_url = "https://example.invalid/demo-sweet-potato.png"
    product.description_en = (
        "Orange-fleshed sweet potato is a short season root crop grown on small plots. "
        + DEMO_NOTE
        + "."
    )
    product.description_rw = (
        "Igitoki cy'umuhondo gikura vuba mu butaka butoshye. " + DEMO_NOTE + "."
    )
    product.how_not_to_use = (
        "Do not eat mouldy roots and do not feed spoiled vines to animals. " + DEMO_NOTE + "."
    )
    product.how_to_preserve = (
        "Store in a dry shaded place and use within three weeks. " + DEMO_NOTE + "."
    )
    product.conditions_diseases = (
        "Supports vitamin A intake, which matters for young children. " + DEMO_NOTE + "."
    )
    product.reviewer_name = "Demo seed"
    product.reviewer_organisation = "AGUra development"
    product.review_date = date.today()
    product.needs_expert_review = True
    product.growing_steps_validated = True
    product.time_to_harvest_min_days = 90
    product.time_to_harvest_max_days = 120
    product.harvest_method = "Lift the roots with a fork, do not cut them. " + DEMO_NOTE + "."
    product.irrigation_method = (
        "Water at the base of the plant, twice a day in the dry season. " + DEMO_NOTE + "."
    )
    product.irrigation_frequency = IrrigationFrequency.DAILY
    product.irrigation_time_of_day = TimeOfDay.MORNING
    product.place_type = "open_field"
    product.temperature_min_c = 18
    product.temperature_max_c = 30
    product.humidity_min_pct = 50
    product.humidity_max_pct = 80
    product.resources = RESOURCES
    product.recipes = RECIPES
    product.season_suitability = {
        "season_a": "suitable",
        "season_b": "suitable",
        "season_c": "marginal",
    }
    product.spray_details = {
        "product": None,
        "dose": None,
        "interval": None,
        "safety_note": DEMO_NOTE,
    }

    db.add(product)
    db.flush()

    existing = {benefit.age_group for benefit in product.benefits}
    for group in AGE_GROUPS:
        if group not in existing:
            db.add(
                ProductBenefit(
                    product_id=product.id,
                    age_group=group,
                    benefit_text="Demo benefit text for " + group.value + ". " + DEMO_NOTE + ".",
                )
            )

    if not product.nutrients:
        db.add_all(_nutrients(product))

    if not product.schedule_tasks:
        for task_type, day_offset, repeat_rule, weekday, until, time_of_day, text in SCHEDULE:
            db.add(
                ProductScheduleTask(
                    product_id=product.id,
                    task_type=task_type,
                    day_offset=day_offset,
                    repeat_rule=repeat_rule,
                    repeat_weekday=weekday,
                    repeat_interval_days=7 if repeat_rule is RepeatRule.WEEKLY else None,
                    repeat_until_day=until,
                    time_of_day=time_of_day,
                    instruction_text=text,
                    reminder_enabled=True,
                )
            )

    db.commit()
    db.refresh(product)

    # Publishing goes through the same rule the console uses, so the demo also
    # proves the trackable gate rather than bypassing it.
    return product_service.mark_trackable(db, product, None)


def _nutrients(product: Product) -> list:
    from app.models.product import ProductNutrient

    return [
        ProductNutrient(
            product_id=product.id,
            nutrient="vitamin_a_rae",
            amount_per_100g=709.0,
            unit="ug",
            source=DEMO_NOTE,
        ),
        ProductNutrient(
            product_id=product.id,
            nutrient="energy_kcal",
            amount_per_100g=86.0,
            unit="kcal",
            source=DEMO_NOTE,
        ),
        ProductNutrient(
            product_id=product.id,
            nutrient="fibre_g",
            amount_per_100g=3.0,
            unit="g",
            source=DEMO_NOTE,
        ),
    ]


__all__ = ["DEMO_NOTE", "NAME_EN", "demo_product", "seed_demo_product"]


def main() -> int:
    from app.db import get_session_factory

    session = get_session_factory()()
    try:
        seeded = seed_demo_product(session)
        print(f"{seeded.name_en}: {seeded.status.value} version {seeded.version}")
    finally:
        session.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
