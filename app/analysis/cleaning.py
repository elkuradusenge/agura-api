"""Chunked cleaning with pandas, then staging, then SQL deduplication."""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.analysis import loading
from app.analysis.constants import (
    AREA_ALIASES,
    PROVINCE_ALIASES,
    SMALL_WORDS,
    is_household_variable,
    is_person_variable,
)
from app.analysis.dictionary import DictionaryEntry, resolve_encoding
from app.analysis.reader import iter_dta_chunks

logger = logging.getLogger("agura.analysis.cleaning")

HOUSEHOLD_CORE_ORDER = ("hhid", "ml01", "ml02", "ml03", "ml07", "hh_weight", "pop_weight")
PERSON_CORE_ORDER = ("hhid", "p01", "p03", "p04")
AGE_VARIABLE = "p04"
MIN_AGE = 0
MAX_AGE = 120


@dataclass
class CleaningStats:
    rows_raw: int = 0
    exact_duplicate_rows: int = 0
    missing_codes_to_null: int = 0
    blanks_to_null: int = 0
    geography_standardised: int = 0
    invalid_ages: int = 0
    household_stage_rows: int = 0
    person_stage_rows: int = 0
    duplicate_households: int = 0
    duplicate_persons: int = 0
    person_level: bool = False
    null_counts: dict[str, int] = field(default_factory=dict)

    def merge(self, other: CleaningStats) -> None:
        self.rows_raw += other.rows_raw
        self.exact_duplicate_rows += other.exact_duplicate_rows
        self.missing_codes_to_null += other.missing_codes_to_null
        self.blanks_to_null += other.blanks_to_null
        self.geography_standardised += other.geography_standardised
        self.invalid_ages += other.invalid_ages
        self.person_level = self.person_level or other.person_level
        for variable, count in other.null_counts.items():
            self.null_counts[variable] = self.null_counts.get(variable, 0) + count


@dataclass
class StagedCleaning:
    tables: loading.StagingTables
    stats: CleaningStats
    conflicts: int = 0
    household_rows_read: int = 0
    person_rows_read: int = 0


@dataclass
class CleaningResult:
    rows_raw: int
    rows_after_dedup: int
    exact_duplicate_rows: int
    duplicate_households: int
    duplicate_persons: int
    household_variable_conflicts: int
    household_rows_read: int
    households_loaded: int
    persons_loaded: int
    person_level: bool
    rule_violations: list[dict[str, Any]]
    missing_rates: dict[str, float]
    warnings: list[str]


def _is_number(text_value: str) -> bool:
    try:
        float(text_value)
    except (TypeError, ValueError):
        return False
    return True


def _decode_codes(series: pd.Series, entry: DictionaryEntry) -> pd.Series:
    """Turn numeric codes into their value labels when the dictionary has labels."""
    if not entry.has_value_labels or not pd.api.types.is_numeric_dtype(series):
        return series
    mapping = {
        float(item.value): item.label for item in entry.value_labels if _is_number(item.value)
    }
    return series.map(lambda value: mapping.get(float(value)) if pd.notna(value) else None)


def title_case_name(value: str) -> str:
    words = " ".join(value.split()).split(" ")
    formatted = []
    for position, word in enumerate(words):
        if position > 0 and word.lower() in SMALL_WORDS:
            formatted.append(word.lower())
        elif word:
            formatted.append(word[:1].upper() + word[1:].lower())
        else:
            formatted.append(word)
    return " ".join(formatted)


def standardise_geography(series: pd.Series, variable_name: str) -> pd.Series:
    text_series = series.astype("string").str.strip().str.replace(r"\s+", " ", regex=True)
    text_series = text_series.replace({"": pd.NA})
    lowered = text_series.str.lower()
    formatted = text_series.map(
        lambda value: title_case_name(str(value)) if pd.notna(value) else value
    )
    if variable_name == "ml01":
        return lowered.map(PROVINCE_ALIASES).fillna(formatted)
    if variable_name == "ml07":
        return lowered.map(AREA_ALIASES).fillna(formatted)
    return formatted


def normalise_chunk(
    frame: pd.DataFrame, dictionary: dict[str, DictionaryEntry]
) -> tuple[pd.DataFrame, CleaningStats]:
    """Apply the documented cleaning rules to one chunk of the file."""
    stats = CleaningStats()
    normalised_names = [str(column).strip().lower() for column in frame.columns]
    keep_positions = [
        position for position, name in enumerate(normalised_names) if name in dictionary
    ]
    dropped = len(normalised_names) - len(keep_positions)
    if dropped:
        logger.info("Ignoring %s columns that are not in the dictionary", dropped)
    # Take a copy of the kept columns only, so the full width chunk can be freed.
    working = frame.iloc[:, keep_positions].copy()
    working.columns = [normalised_names[position] for position in keep_positions]

    for variable in working.columns:
        entry = dictionary[variable]
        series = working[variable]

        if entry.missing_codes:
            if pd.api.types.is_numeric_dtype(series):
                codes = [float(code) for code in entry.missing_codes if _is_number(code)]
                mask = series.isin(codes)
            else:
                mask = series.astype("string").str.strip().isin(entry.missing_codes)
            replaced = int(mask.fillna(False).sum())
            if replaced:
                working[variable] = series.mask(mask)
                series = working[variable]
                stats.missing_codes_to_null += replaced

        if pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series):
            text_series = series.astype("string").str.strip()
            blank_mask = text_series == ""
            blanks = int(blank_mask.fillna(False).sum())
            if blanks:
                text_series = text_series.mask(blank_mask)
                stats.blanks_to_null += blanks
            working[variable] = text_series
            series = working[variable]

        if entry.section == "geography":
            decoded = _decode_codes(series, entry)
            standardised = standardise_geography(decoded, variable)
            changed = int((standardised.fillna("") != decoded.astype("string").fillna("")).sum())
            if changed:
                stats.geography_standardised += changed
            working[variable] = standardised
            series = working[variable]

        nulls = int(series.isna().sum())
        if nulls:
            stats.null_counts[variable] = nulls

    if AGE_VARIABLE in working.columns:
        ages = pd.to_numeric(working[AGE_VARIABLE], errors="coerce")
        invalid = ages.notna() & ((ages < MIN_AGE) | (ages > MAX_AGE))
        invalid_count = int(invalid.sum())
        if invalid_count:
            stats.invalid_ages += invalid_count
            ages = ages.mask(invalid)
        working[AGE_VARIABLE] = ages.astype("Int64")

    stats.rows_raw = len(frame)
    return working, stats


def _scalar(value: Any) -> Any:
    if value is None or value is pd.NA:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if hasattr(value, "item") and not isinstance(value, str | bytes):
        with suppress(ValueError, AttributeError):
            value = value.item()
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _attribute_scalar(value: Any) -> Any:
    """Census codes are whole numbers.

    Storing them as JSON integers keeps `attributes->>'h25_21' = '1'` exact, while a
    JSON float would render as `1.0` and never match.
    """
    scalar = _scalar(value)
    if isinstance(scalar, float) and scalar.is_integer():
        return int(scalar)
    return scalar


def _attributes_frame(frame: pd.DataFrame, columns: list[str]) -> list[str]:
    """Serialise the non core columns of every row as one JSON object per row."""
    if not columns:
        return ["{}"] * len(frame)

    records = frame[columns].astype(object).where(frame[columns].notna(), None).values
    payloads: list[str] = []
    for row in records:
        payload: dict[str, Any] = {}
        for column, value in zip(columns, row, strict=True):
            if value is None:
                continue
            scalar = _attribute_scalar(value)
            if scalar is not None:
                payload[column] = scalar
        payloads.append(json.dumps(payload, default=str))
    return payloads


def _core_values(frame: pd.DataFrame, columns: list[str]) -> list[tuple]:
    if not frame.empty and not columns:
        return [()] * len(frame)
    subset = frame[columns].astype(object).where(pd.notna(frame[columns]), None)
    return list(subset.itertuples(index=False, name=None))


def build_rows(frame: pd.DataFrame) -> tuple[list[tuple], list[tuple]]:
    """Split one cleaned chunk into staging rows for households and persons."""
    household_columns = [column for column in frame.columns if is_household_variable(column)]
    person_columns = [
        column for column in frame.columns if is_person_variable(column) or column == "hhid"
    ]

    household_rows: list[tuple] = []
    if "hhid" in household_columns:
        core_names = [name for name in HOUSEHOLD_CORE_ORDER if name in household_columns]
        attribute_columns = [name for name in household_columns if name not in core_names]
        attributes = _attributes_frame(frame, attribute_columns)
        for values, payload in zip(_core_values(frame, core_names), attributes, strict=True):
            record = dict(zip(core_names, values, strict=True))
            hhid = record.get("hhid")
            if hhid in (None, ""):
                continue
            household_rows.append(
                (
                    str(hhid).strip(),
                    _scalar(record.get("ml01")),
                    _scalar(record.get("ml02")),
                    _scalar(record.get("ml03")),
                    _scalar(record.get("ml07")),
                    _scalar(record.get("hh_weight")),
                    _scalar(record.get("pop_weight")),
                    payload,
                )
            )

    person_rows: list[tuple] = []
    if "p01" in person_columns:
        core_names = [name for name in PERSON_CORE_ORDER if name in person_columns]
        attribute_columns = [name for name in person_columns if name not in core_names]
        attributes = _attributes_frame(frame, attribute_columns)
        for values, payload in zip(_core_values(frame, core_names), attributes, strict=True):
            record = dict(zip(core_names, values, strict=True))
            hhid = record.get("hhid")
            p01 = record.get("p01")
            if hhid in (None, "") or p01 in (None, ""):
                continue
            person_rows.append(
                (
                    str(hhid).strip(),
                    str(p01).strip(),
                    _scalar(record.get("p03")),
                    _scalar(record.get("p04")),
                    payload,
                )
            )

    return household_rows, person_rows


def clear_survey_data(db: Session, survey_id) -> None:
    """Remove previously loaded rows so a resumed or retried job starts clean."""
    db.execute(
        text("DELETE FROM persons WHERE survey_id = :survey_id"), {"survey_id": str(survey_id)}
    )
    db.execute(
        text("DELETE FROM households WHERE survey_id = :survey_id"), {"survey_id": str(survey_id)}
    )
    db.commit()


def run_cleaning(
    *,
    db: Session,
    survey_id,
    job_id,
    path: str,
    dictionary: dict[str, DictionaryEntry],
    chunk_size: int,
    encoding: str | None = None,
    total_rows: int | None = None,
    on_progress: Callable[[int, float], None] | None = None,
) -> StagedCleaning:
    """Stream the file, clean each chunk and copy the rows into staging tables."""
    tables = loading.staging_tables_for(job_id.hex)
    loading.create_staging(db, tables)
    stats = CleaningStats()
    seen_rows: set[int] = set()

    try:
        resolved_encoding = resolve_encoding(path, encoding)
        for frame, _meta in iter_dta_chunks(path, chunk_size, resolved_encoding):
            cleaned, delta = normalise_chunk(frame, dictionary)
            stats.merge(delta)
            if cleaned.empty:
                continue

            # Exact duplicates can sit in different chunks, so the hashes are kept
            # for the whole file. One integer per distinct row keeps memory flat.
            row_hashes = pd.util.hash_pandas_object(cleaned, index=False)
            repeated = row_hashes.isin(seen_rows)
            repeated_count = int(repeated.sum())
            seen_rows.update(int(value) for value in row_hashes.unique())
            if repeated_count:
                stats.exact_duplicate_rows += repeated_count
                cleaned = cleaned.loc[~repeated.to_numpy()]
            if cleaned.empty:
                continue

            household_rows, person_rows = build_rows(cleaned)
            if person_rows:
                stats.person_level = True
            stats.household_stage_rows += loading.copy_households(db, tables, household_rows)
            stats.person_stage_rows += loading.copy_persons(db, tables, person_rows)

            if on_progress:
                percent = 0.0
                if total_rows:
                    percent = min(99.0, round(stats.rows_raw * 100.0 / total_rows, 1))
                on_progress(stats.rows_raw, percent)
    except Exception:
        loading.drop_staging(db, tables)
        raise

    household_stage_total = loading.count_rows(db, tables.households)
    person_stage_total = loading.count_rows(db, tables.persons)
    # Counted on hhid alone so the report reconciles with the households table:
    # household rows read minus repeated rows equals households loaded.
    stats.duplicate_households = household_stage_total - loading.count_distinct(
        db, tables.households, "hhid"
    )
    stats.duplicate_persons = person_stage_total - loading.count_distinct(
        db, tables.persons, "hhid, p01"
    )
    conflicts = loading.count_household_conflicts(db, tables)

    return StagedCleaning(
        tables=tables,
        stats=stats,
        conflicts=conflicts,
        household_rows_read=household_stage_total,
        person_rows_read=person_stage_total,
    )


def build_quality_result(
    staged: StagedCleaning, households_loaded: int, persons_loaded: int
) -> CleaningResult:
    stats = staged.stats
    dropped = stats.exact_duplicate_rows + (
        stats.duplicate_persons if stats.person_level else stats.duplicate_households
    )
    rows_after_dedup = max(stats.rows_raw - dropped, 0)

    rule_violations = [
        {
            "rule": "declared_missing_codes_to_null",
            "count": stats.missing_codes_to_null,
            "detail": "Values the dictionary marks as missing were stored as NULL",
        },
        {
            "rule": "blank_text_to_null",
            "count": stats.blanks_to_null,
            "detail": "Empty or whitespace only text was stored as NULL",
        },
        {
            "rule": "geography_name_standardised",
            "count": stats.geography_standardised,
            "detail": "Province, district, sector and area names were trimmed and canonicalised",
        },
        {
            "rule": "impossible_age_to_null",
            "count": stats.invalid_ages,
            "detail": f"Age outside {MIN_AGE} to {MAX_AGE} was stored as NULL",
        },
        {
            "rule": "exact_duplicate_rows_removed",
            "count": stats.exact_duplicate_rows,
            "detail": "Rows identical on every mapped column were kept once",
        },
        {
            "rule": "repeated_household_rows_collapsed",
            "count": stats.duplicate_households,
            "detail": (
                "The file repeats the household variables on every person row, so "
                f"{staged.household_rows_read} household rows became "
                f"{staged.household_rows_read - stats.duplicate_households} households"
            ),
        },
        {
            "rule": "duplicate_person_records_removed",
            "count": stats.duplicate_persons,
            "detail": "Repeated person records were kept once per hhid and p01",
        },
        {
            "rule": "household_variable_conflicts",
            "count": staged.conflicts,
            "detail": "Households whose repeated rows disagree on household variables",
        },
    ]

    missing_rates = {
        variable: round(count / stats.rows_raw, 4)
        for variable, count in stats.null_counts.items()
        if stats.rows_raw and count
    }

    warnings: list[str] = []
    if not stats.person_level:
        warnings.append("No person level records found, only household records were loaded")
    if staged.conflicts:
        warnings.append(
            f"{staged.conflicts} households report different household variables on different rows"
        )
    high_missing = [
        variable
        for variable, rate in missing_rates.items()
        if rate >= 0.5 and variable != AGE_VARIABLE
    ]
    if high_missing:
        warnings.append(
            f"{len(high_missing)} mapped variables are missing in at least half of the rows"
        )
    if stats.invalid_ages:
        warnings.append(f"{stats.invalid_ages} ages were outside the accepted range")

    return CleaningResult(
        rows_raw=stats.rows_raw,
        rows_after_dedup=rows_after_dedup,
        exact_duplicate_rows=stats.exact_duplicate_rows,
        duplicate_households=stats.duplicate_households,
        duplicate_persons=stats.duplicate_persons,
        household_variable_conflicts=staged.conflicts,
        household_rows_read=staged.household_rows_read,
        households_loaded=households_loaded,
        persons_loaded=persons_loaded,
        person_level=stats.person_level,
        rule_violations=rule_violations,
        missing_rates=missing_rates,
        warnings=warnings,
    )


def run_loading(db: Session, survey_id, staged: StagedCleaning) -> CleaningResult:
    """Deduplicate in SQL and write the warehouse tables.

    Secondary indexes are dropped for the duration of the insert and rebuilt
    afterwards: building them once is much cheaper than maintaining them row by row.
    """
    definitions: list[str] = []
    try:
        clear_survey_data(db, survey_id)
        definitions = loading.defer_secondary_indexes(db)
        households_loaded = loading.finalise_households(db, survey_id, staged.tables)
        persons_loaded = loading.finalise_persons(db, survey_id, staged.tables)
        if staged.stats.person_level:
            loading.update_household_sizes(db, survey_id)
        db.commit()
    finally:
        if definitions:
            loading.recreate_indexes(db, definitions)
        loading.analyse_loaded_tables(db)
        db.commit()
        loading.drop_staging(db, staged.tables)

    return build_quality_result(staged, households_loaded, persons_loaded)
