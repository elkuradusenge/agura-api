"""Automatic field mapping proposal, reuse across surveys and admin confirmation."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.constants import platform_group_for
from app.analysis.dictionary import DictionaryEntry
from app.core.errors import unprocessable
from app.models.enums import PlatformGroup
from app.models.survey import FieldMapping, Survey

logger = logging.getLogger("agura.analysis.mapping")

FLAG_VARIABLE_PATTERN = re.compile(
    r"^(h21_\d{2}|h22a_\d{2}|h24_\d{2}|h25_\d{2}|h28b1_\d{2}|h22|h23|h28a)$"
)


def looks_like_flag(variable_name: str) -> bool:
    """Counts (h22b1_, h22b2_, h28b2_, h26, h27) are not flags."""
    return bool(FLAG_VARIABLE_PATTERN.match(variable_name.lower()))


def _reusable_mapping(db: Session, survey_id: uuid.UUID) -> dict[str, FieldMapping]:
    """Take the most recently confirmed mapping from an earlier survey."""
    rows = db.scalars(
        select(FieldMapping)
        .join(Survey, Survey.id == FieldMapping.survey_id)
        .where(
            FieldMapping.confirmed.is_(True),
            FieldMapping.survey_id != survey_id,
            Survey.deleted_at.is_(None),
        )
        .order_by(FieldMapping.confirmed_at.desc())
    ).all()
    reusable: dict[str, FieldMapping] = {}
    for row in rows:
        reusable.setdefault(row.variable_name, row)
    return reusable


def propose_mappings(
    db: Session, survey_id: uuid.UUID, entries: list[DictionaryEntry]
) -> list[FieldMapping]:
    reusable = _reusable_mapping(db, survey_id)
    existing = {
        row.variable_name: row
        for row in db.scalars(select(FieldMapping).where(FieldMapping.survey_id == survey_id))
    }

    proposals: list[FieldMapping] = []
    for entry in entries:
        group = platform_group_for(entry.variable_name)
        yes_code = entry.yes_code
        yes_label = entry.yes_label
        reused = reusable.get(entry.variable_name)

        if reusable and not entry.has_value_labels and reused and reused.yes_code:
            yes_code, yes_label = reused.yes_code, reused.yes_label
        if yes_code is None and not entry.has_value_labels and looks_like_flag(entry.variable_name):
            # No labels in the file: show the common yes code and let the admin confirm it.
            yes_code, yes_label = "1", "Assumed yes"

        row = existing.get(entry.variable_name)
        if row is None:
            row = FieldMapping(
                survey_id=survey_id,
                variable_name=entry.variable_name,
                platform_group=group,
                yes_code=yes_code,
                yes_label=yes_label,
            )
            db.add(row)
        elif not row.confirmed:
            row.platform_group = group
            row.yes_code = yes_code
            row.yes_label = yes_label
        proposals.append(row)

    db.flush()
    return proposals


def confirmed_yes_codes(db: Session, survey_id: uuid.UUID) -> dict[str, str]:
    rows = db.scalars(
        select(FieldMapping).where(
            FieldMapping.survey_id == survey_id,
            FieldMapping.confirmed.is_(True),
            FieldMapping.yes_code.is_not(None),
        )
    ).all()
    return {row.variable_name: str(row.yes_code) for row in rows}


def is_mapping_confirmed(db: Session, survey: Survey) -> bool:
    return survey.mapping_confirmed_at is not None


def confirm_mappings(
    db: Session,
    survey: Survey,
    admin_id: uuid.UUID | None,
    updates: list[dict[str, str | None]],
) -> list[FieldMapping]:
    rows = {
        row.variable_name: row
        for row in db.scalars(select(FieldMapping).where(FieldMapping.survey_id == survey.id))
    }
    if not rows:
        raise unprocessable(
            "job.mapping_not_proposed",
            "errors.job.mapping_not_proposed",
            "The mapping has not been proposed yet, the job must reach the mapped stage",
        )

    for update in updates:
        variable = str(update["variable_name"]).strip().lower()
        row = rows.get(variable)
        if row is None:
            raise unprocessable(
                "job.unknown_variable",
                "errors.job.unknown_variable",
                f"Variable {variable} is not part of this survey",
            )
        if update.get("platform_group"):
            try:
                row.platform_group = PlatformGroup(str(update["platform_group"]))
            except ValueError as exc:
                raise unprocessable(
                    "job.invalid_group",
                    "errors.job.invalid_group",
                    f"Unknown platform group {update['platform_group']}",
                ) from exc
        if "yes_code" in update:
            yes_code = update.get("yes_code")
            row.yes_code = str(yes_code) if yes_code not in (None, "") else None
            if row.yes_code is not None:
                row.yes_label = row.yes_label or "Set by administrator"

    confirmed_at = datetime.now(UTC)
    for row in rows.values():
        row.confirmed = True
        row.confirmed_by_id = admin_id
        row.confirmed_at = confirmed_at
    survey.mapping_confirmed_at = confirmed_at
    db.commit()
    return list(rows.values())


def mapping_table(db: Session, survey_id: uuid.UUID) -> list[FieldMapping]:
    return list(
        db.scalars(
            select(FieldMapping)
            .where(FieldMapping.survey_id == survey_id)
            .order_by(FieldMapping.variable_name)
        )
    )
