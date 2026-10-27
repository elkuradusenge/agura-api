"""Read the questionnaire dictionary from the .dta file and store it."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

import pyreadstat
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.analysis.constants import (
    MISSING_LABELS,
    NO_LABELS,
    YES_LABELS,
    platform_group_for,
    section_for,
)
from app.models.survey import ValueLabel, VariableDictionary

logger = logging.getLogger("agura.analysis.dictionary")


class SurveyReadError(Exception):
    """The file could not be opened as a Stata data file."""


@dataclass(frozen=True)
class LabelValue:
    value: str
    label: str
    is_yes: bool = False
    is_missing: bool = False


@dataclass
class DictionaryEntry:
    variable_name: str
    label: str | None
    section: str
    dtype: str | None
    position: int
    value_labels: tuple[LabelValue, ...] = ()
    missing_codes: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        if not self.missing_codes:
            self.missing_codes = tuple(item.value for item in self.value_labels if item.is_missing)

    @property
    def yes_code(self) -> str | None:
        for item in self.value_labels:
            if item.is_yes:
                return item.value
        return None

    @property
    def yes_label(self) -> str | None:
        for item in self.value_labels:
            if item.is_yes:
                return item.label
        return None

    @property
    def has_value_labels(self) -> bool:
        return bool(self.value_labels)


def value_to_text(value: object) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def classify_label(label: str) -> str | None:
    normalised = " ".join(str(label).strip().lower().split())
    if normalised in NO_LABELS:
        return "no"
    if normalised in YES_LABELS:
        return "yes"
    if normalised in MISSING_LABELS:
        return "missing"
    if normalised.startswith("yes") or normalised.startswith("grown"):
        return "yes"
    return None


ENCODING_CANDIDATES: tuple[str | None, ...] = (None, "cp1252", "latin-1")
_ENCODING_CACHE: dict[str, str | None] = {}


def _read_metadata_with(path: str, encoding: str | None):
    kwargs: dict[str, object] = {"metadataonly": True}
    if encoding:
        kwargs["encoding"] = encoding
    _, meta = pyreadstat.read_dta(path, **kwargs)
    return meta


def resolve_encoding(path: str, preferred: str | None = None) -> str | None:
    """Stata files in the wild are not always UTF-8, so keep the first encoding that works."""
    if path in _ENCODING_CACHE:
        return _ENCODING_CACHE[path]
    candidates = (preferred,) if preferred else ENCODING_CANDIDATES
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            _read_metadata_with(path, candidate)
        except Exception as exc:  # pyreadstat raises several unrelated error types
            last_error = exc
            continue
        _ENCODING_CACHE[path] = candidate
        return candidate
    raise SurveyReadError(str(last_error))


def read_metadata(path: str, encoding: str | None = None):
    resolved = resolve_encoding(path, encoding)
    try:
        return _read_metadata_with(path, resolved)
    except Exception as exc:
        raise SurveyReadError(str(exc)) from exc


def build_dictionary(meta) -> list[DictionaryEntry]:
    value_labels_by_variable = getattr(meta, "variable_value_labels", {}) or {}
    original_types = getattr(meta, "original_variable_types", {}) or {}
    labels = getattr(meta, "column_labels", None) or []
    entries: list[DictionaryEntry] = []

    for position, raw_name in enumerate(meta.column_names):
        name = str(raw_name).strip().lower()
        label = labels[position] if position < len(labels) else None
        label = str(label).strip() if label else None
        labels_for_variable = value_labels_by_variable.get(raw_name) or {}

        label_values = []
        missing_codes = []
        for value, value_label in labels_for_variable.items():
            text = value_to_text(value)
            classification = classify_label(value_label)
            is_yes = classification == "yes"
            is_missing = classification == "missing"
            label_values.append(
                LabelValue(
                    value=text, label=str(value_label).strip(), is_yes=is_yes, is_missing=is_missing
                )
            )
            if is_missing:
                missing_codes.append(text)

        entries.append(
            DictionaryEntry(
                variable_name=name,
                label=label,
                section=section_for(name),
                dtype=original_types.get(raw_name),
                position=position,
                value_labels=tuple(label_values),
                missing_codes=tuple(missing_codes),
            )
        )
    return entries


def store_dictionary(db: Session, survey_id: uuid.UUID, entries: list[DictionaryEntry]) -> int:
    """Replace the stored dictionary for a survey. Only dictionary rows come from the file."""
    db.execute(delete(VariableDictionary).where(VariableDictionary.survey_id == survey_id))
    for entry in entries:
        row = VariableDictionary(
            survey_id=survey_id,
            variable_name=entry.variable_name,
            label=entry.label,
            section=entry.section,
            dtype=entry.dtype,
            position=entry.position,
        )
        db.add(row)
        db.flush()
        for item in entry.value_labels:
            db.add(
                ValueLabel(
                    dictionary_id=row.id,
                    value_text=item.value,
                    label=item.label,
                    is_yes=item.is_yes,
                    is_missing=item.is_missing,
                )
            )
    db.flush()
    return len(entries)


def load_dictionary(db: Session, survey_id: uuid.UUID) -> dict[str, DictionaryEntry]:
    rows = db.scalars(
        select(VariableDictionary)
        .where(VariableDictionary.survey_id == survey_id)
        .order_by(VariableDictionary.position)
    ).all()
    entries: dict[str, DictionaryEntry] = {}
    label_rows = (
        db.scalars(
            select(ValueLabel).where(ValueLabel.dictionary_id.in_([row.id for row in rows]))
        ).all()
        if rows
        else []
    )
    labels_by_dictionary: dict[uuid.UUID, list[LabelValue]] = {}
    for label_row in label_rows:
        labels_by_dictionary.setdefault(label_row.dictionary_id, []).append(
            LabelValue(
                value=label_row.value_text,
                label=label_row.label,
                is_yes=label_row.is_yes,
                is_missing=label_row.is_missing,
            )
        )

    for row in rows:
        value_labels = tuple(labels_by_dictionary.get(row.id, ()))
        entries[row.variable_name] = DictionaryEntry(
            variable_name=row.variable_name,
            label=row.label,
            section=row.section or section_for(row.variable_name),
            dtype=row.dtype,
            position=row.position,
            value_labels=value_labels,
            missing_codes=tuple(item.value for item in value_labels if item.is_missing),
        )
    return entries


def platform_groups(entries: list[DictionaryEntry]) -> dict[str, str]:
    return {entry.variable_name: platform_group_for(entry.variable_name).value for entry in entries}


def code_for_keywords(entry: DictionaryEntry | None, keywords: set[str]) -> str | None:
    """Find the value label that means the same as one of the keywords.

    Nothing is assumed about the numeric code: it always comes from the file labels.
    """
    if entry is None:
        return None
    for item in entry.value_labels:
        normalised = " ".join(item.label.strip().lower().split())
        if normalised in keywords or any(word in normalised for word in keywords):
            return item.value
    return None


def codes_for_keywords(entry: DictionaryEntry | None, keywords: set[str]) -> list[str]:
    if entry is None:
        return []
    found: list[str] = []
    for item in entry.value_labels:
        normalised = " ".join(item.label.strip().lower().split())
        if any(word in normalised for word in keywords):
            found.append(item.value)
    return found
