"""Nutrition reference import and lookup.

The table starts empty on purpose: nutrient values and health statements may only come
from an expert, never from the pipeline. This module only moves numbers that a human
supplied.
"""

from __future__ import annotations

import csv
import io
import uuid
from dataclasses import dataclass, field

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.errors import unprocessable
from app.models.household import FoodItem
from app.models.ranking import NutritionReference

REQUIRED_COLUMNS = ("food_variable", "nutrient", "amount_per_100g")
OPTIONAL_COLUMNS = ("unit", "source", "reviewer_name", "review_date")

NUTRIENT_ALIASES = {
    "iron": "iron",
    "zinc": "zinc",
    "vitamin a": "vitamin_a",
    "vitamin_a": "vitamin_a",
    "retinol": "vitamin_a",
    "protein": "protein",
    "fibre": "fibre",
    "fiber": "fibre",
    "vitamin c": "vitamin_c",
    "vitamin_c": "vitamin_c",
    "calcium": "calcium",
    "folate": "folate",
}

TRACKED_NUTRIENTS = ("iron", "zinc", "vitamin_a", "protein")


@dataclass
class ImportResult:
    imported: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "imported": self.imported,
            "skipped": self.skipped,
            "errors": self.errors[:50],
        }


def normalise_nutrient(name: str) -> str:
    key = " ".join(str(name).strip().lower().split())
    return NUTRIENT_ALIASES.get(key, key.replace(" ", "_"))


def import_nutrition_csv(db: Session, content: bytes) -> ImportResult:
    """Replace nutrient rows from a CSV, keyed on food variable plus nutrient."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise unprocessable(
            "nutrition.invalid_encoding",
            "errors.nutrition.invalid_encoding",
            "The CSV must be UTF-8 encoded",
        ) from exc

    reader = csv.DictReader(io.StringIO(text))
    header = {(name or "").strip().lower() for name in (reader.fieldnames or [])}
    missing = [column for column in REQUIRED_COLUMNS if column not in header]
    if missing:
        raise unprocessable(
            "nutrition.missing_columns",
            "errors.nutrition.missing_columns",
            "The CSV is missing required columns: " + ", ".join(missing),
            {"missing": missing},
        )

    by_variable = {item.source_variable.lower(): item for item in db.scalars(select(FoodItem))}
    result = ImportResult()

    for line_number, row in enumerate(reader, start=2):
        variable = (row.get("food_variable") or "").strip().lower()
        if not variable:
            result.skipped += 1
            result.errors.append(f"line {line_number}: food_variable is empty")
            continue
        item = by_variable.get(variable)
        if item is None:
            result.skipped += 1
            result.errors.append(f"line {line_number}: unknown food variable {variable}")
            continue

        amount_text = (row.get("amount_per_100g") or "").strip()
        amount: float | None = None
        if amount_text:
            try:
                amount = float(amount_text)
            except ValueError:
                result.skipped += 1
                result.errors.append(
                    f"line {line_number}: amount_per_100g is not a number: {amount_text}"
                )
                continue
            if amount < 0:
                result.skipped += 1
                result.errors.append(f"line {line_number}: amount_per_100g is negative")
                continue

        nutrient = normalise_nutrient(row.get("nutrient") or "")
        if not nutrient:
            result.skipped += 1
            result.errors.append(f"line {line_number}: nutrient is empty")
            continue

        existing = db.scalar(
            select(NutritionReference).where(
                NutritionReference.food_item_id == item.id,
                NutritionReference.nutrient == nutrient,
            )
        )
        record = existing or NutritionReference(food_item_id=item.id, nutrient=nutrient)
        record.amount_per_100g = amount
        record.unit = (row.get("unit") or "").strip() or None
        record.source = (row.get("source") or "").strip() or None
        record.reviewer_name = (row.get("reviewer_name") or "").strip() or None
        if existing is None:
            db.add(record)
        result.imported += 1

    db.commit()
    return result


def nutrition_by_food(db: Session) -> dict[uuid.UUID, dict[str, float]]:
    """Nutrient amounts per food, only for nutrients with a value."""
    rows = db.execute(
        select(
            NutritionReference.food_item_id,
            NutritionReference.nutrient,
            NutritionReference.amount_per_100g,
        )
    ).all()
    payload: dict[uuid.UUID, dict[str, float]] = {}
    for food_id, nutrient, amount in rows:
        if amount is None:
            continue
        payload.setdefault(food_id, {})[nutrient] = float(amount)
    return payload


def clear_nutrition(db: Session, food_item_id: uuid.UUID | None = None) -> int:
    statement = delete(NutritionReference)
    if food_item_id is not None:
        statement = statement.where(NutritionReference.food_item_id == food_item_id)
    result = db.execute(statement)
    db.commit()
    return result.rowcount or 0


def nutrition_is_empty(db: Session) -> bool:
    return not db.scalar(select(NutritionReference.id).limit(1))


def nutrition_value(
    amounts: dict[str, float] | None, need_weights: dict[str, float]
) -> float | None:
    """A 0 to 1 nutrient density for one food, using the nutrients the admins track.

    Only nutrients with a need weight contribute, so the score reflects what the
    programme is trying to improve. Returning None keeps "unknown" distinct from zero.
    """
    if not amounts:
        return None
    total_weight = 0.0
    total = 0.0
    for nutrient, need in need_weights.items():
        amount = amounts.get(nutrient)
        if amount is None or need <= 0:
            continue
        # 10 mg per 100 g is treated as fully meeting a daily need for the index.
        total += min(amount / REFERENCE_DAILY_AMOUNT.get(nutrient, 10.0), 1.0) * need
        total_weight += need
    if total_weight == 0:
        return None
    return total / total_weight


REFERENCE_DAILY_AMOUNT = {
    "iron": 10.0,
    "zinc": 8.0,
    "vitamin_a": 0.6,
    "protein": 20.0,
    "fibre": 12.0,
    "vitamin_c": 45.0,
    "calcium": 600.0,
    "folate": 200.0,
}
