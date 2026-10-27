"""Application settings stored in the database and editable by administrators."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.app_settings import AppSetting

DEFAULT_SETTINGS: dict[str, tuple[Any, str]] = {
    "privacy.small_cell_threshold": (
        10,
        "Aggregates built from fewer households than this are suppressed everywhere.",
    ),
    "reminders.quiet_hours": (
        {"start": "21:00", "end": "05:00", "urgent_override": True},
        "Quiet hours in Africa/Kigali. Urgent tasks may bypass them.",
    ),
    "reminders.max_messages_per_day": (
        3,
        "Maximum reminder messages per farmer per day unless the farmer opts in.",
    ),
    "reminders.missed_task_follow_ups": (1, "Number of follow up reminders for a missed task."),
    "analysis.source_name": ("Rwanda Population and Housing Census 2022", "Shown with results."),
    "analysis.source_year": (2022, "Shown with results."),
    "analysis.nutrition_need_weights": (
        {"iron": 1.0, "zinc": 1.0, "vitamin_a": 1.0, "protein": 1.0},
        "Nutrition need weights set by experts.",
    ),
    "ai.ranking_weights": (
        {"nutrition_value": 0.3, "nutrition_need": 0.3, "feasibility": 0.2, "gap": 0.2},
        "Weights for the under farmed food ranking.",
    ),
    "readiness.weights": (
        {
            "shelter": 0.2,
            "water": 0.15,
            "electricity": 0.15,
            "phone": 0.1,
            "internet": 0.1,
            "education": 0.2,
            "household_size": 0.1,
        },
        "Transparent weights for the 0 to 100 readiness index.",
    ),
    "readiness.definitions": (
        {
            "shelter": {
                "variable": "h06",
                "label_keywords": ["brick", "concrete", "cement", "block", "stone", "burnt"],
                "roof_variable": "h05",
                "roof_label_keywords": ["iron", "tile", "concrete", "sheet", "metal"],
            },
            "water": {
                "variable": "h12",
                "label_keywords": ["piped", "protected", "tap", "borehole"],
            },
            "electricity": {"variable": "h14", "label_keywords": ["yes"]},
            "phone": {
                "variable": "p36c",
                "label_keywords": ["smart", "feature"],
            },
            "internet": {
                "variable": "p35",
                "label_keywords": ["yes", "daily", "weekly", "monthly", "occasionally"],
            },
            "education": {
                "variable": "p30a",
                "label_keywords": [
                    "secondary",
                    "university",
                    "higher",
                    "tvet",
                    "vocational",
                    "tertiary",
                ],
            },
        },
        "How each readiness component is detected, taken from the value labels.",
    ),
}


def seed_defaults(db: Session, *, overwrite: bool = False) -> int:
    existing = set(db.scalars(select(AppSetting.key)))
    created = 0
    for key, (value, description) in DEFAULT_SETTINGS.items():
        if key in existing and not overwrite:
            continue
        setting = db.get(AppSetting, key)
        if setting is None:
            setting = AppSetting(key=key, value=value, description=description)
            db.add(setting)
        else:
            setting.value = value
            setting.description = description
        created += 1
    db.commit()
    return created


def get_value(db: Session, key: str, default: Any = None) -> Any:
    setting = db.get(AppSetting, key)
    if setting is None:
        return default
    return setting.value


def set_value(
    db: Session, key: str, value: Any, *, updated_by_id: uuid.UUID | None = None
) -> AppSetting:
    setting = db.get(AppSetting, key)
    if setting is None:
        setting = AppSetting(key=key, value=value, updated_by_id=updated_by_id)
        db.add(setting)
    else:
        setting.value = value
        setting.updated_by_id = updated_by_id
    db.commit()
    return setting


def small_cell_threshold(db: Session) -> int:
    value = get_value(db, "privacy.small_cell_threshold")
    if isinstance(value, int) and value > 0:
        return value
    return get_settings().small_cell_threshold


def all_settings(db: Session) -> list[dict[str, Any]]:
    """Every setting with its description, for the settings screen."""
    rows = db.scalars(select(AppSetting).order_by(AppSetting.key)).all()
    return [{"key": row.key, "value": row.value, "description": row.description} for row in rows]
