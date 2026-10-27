"""Reference data seeded from the questionnaire variable list."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.constants import FOOD_VARIABLES
from app.models.household import FoodItem


def seed_food_items(db: Session, *, overwrite: bool = False) -> int:
    """Create one food_items row per questionnaire food variable.

    Local names are left empty on purpose: Kinyarwanda names are entered by the
    content team after RAB review, never generated.
    """
    existing = {row.source_variable for row in db.scalars(select(FoodItem))}
    written = 0
    for source_variable, standard_name, group, count_variable in FOOD_VARIABLES:
        if source_variable in existing:
            if not overwrite:
                continue
            item = db.scalar(select(FoodItem).where(FoodItem.source_variable == source_variable))
            assert item is not None
            item.standard_name = standard_name
            item.group = group
            item.count_variable = count_variable
            item.nutrition_group = group.value
        else:
            db.add(
                FoodItem(
                    standard_name=standard_name,
                    local_name=None,
                    group=group,
                    source_variable=source_variable,
                    count_variable=count_variable,
                    nutrition_group=group.value,
                )
            )
        written += 1
    db.commit()
    return written
