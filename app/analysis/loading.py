"""Bulk loading of cleaned rows into staging tables and then the warehouse tables.

Rows are streamed with COPY inside a transaction per batch. Staging tables are
unlogged and named after the job, so two uploads can never mix. Deduplication and
household conflict detection run once in SQL after every batch has arrived, which
keeps memory flat regardless of file size.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger("agura.analysis.loading")

HOUSEHOLD_STAGE_COLUMNS = (
    "hhid",
    "ml01",
    "ml02",
    "ml03",
    "ml07",
    "hh_weight",
    "pop_weight",
    "attributes",
)

PERSON_STAGE_COLUMNS = ("hhid", "p01", "sex", "age", "attributes")


class StagingTables:
    __slots__ = ("suffix", "households", "persons")

    def __init__(self, suffix: str) -> None:
        self.suffix = suffix
        self.households = f"stage_households_{suffix}"
        self.persons = f"stage_persons_{suffix}"


def staging_tables_for(suffix: str) -> StagingTables:
    safe = "".join(char for char in suffix if char.isalnum())[:16]
    return StagingTables(safe)


def create_staging(db: Session, tables: StagingTables) -> None:
    db.execute(text(f"DROP TABLE IF EXISTS {tables.households}"))
    db.execute(text(f"DROP TABLE IF EXISTS {tables.persons}"))
    db.execute(
        text(
            f"""
            CREATE UNLOGGED TABLE {tables.households} (
                hhid text NOT NULL,
                ml01 text,
                ml02 text,
                ml03 text,
                ml07 text,
                hh_weight numeric(18, 4),
                pop_weight numeric(18, 4),
                attributes jsonb NOT NULL DEFAULT '{{}}'::jsonb,
                row_hash text GENERATED ALWAYS AS (md5(attributes::text)) STORED
            )
            """
        )
    )
    db.execute(
        text(
            f"""
            CREATE UNLOGGED TABLE {tables.persons} (
                hhid text NOT NULL,
                p01 text NOT NULL,
                sex text,
                age integer,
                attributes jsonb NOT NULL DEFAULT '{{}}'::jsonb
            )
            """
        )
    )
    db.commit()


def drop_staging(db: Session, tables: StagingTables) -> None:
    try:
        db.rollback()
        db.execute(text(f"DROP TABLE IF EXISTS {tables.households}"))
        db.execute(text(f"DROP TABLE IF EXISTS {tables.persons}"))
        db.commit()
    except Exception:  # pragma: no cover - cleanup must never mask the real error
        db.rollback()
        logger.warning("Could not drop staging tables %s and %s", tables.households, tables.persons)


def _copy(db: Session, table: str, columns: tuple[str, ...], rows: list[tuple]) -> int:
    column_list = ", ".join(columns)
    raw_connection = db.connection().connection.dbapi_connection
    with (
        raw_connection.cursor() as cursor,
        cursor.copy(f"COPY {table} ({column_list}) FROM STDIN") as copy,
    ):
        for row in rows:
            copy.write_row(row)
    db.commit()
    return len(rows)


def copy_households(db: Session, tables: StagingTables, rows: list[tuple]) -> int:
    if not rows:
        return 0
    return _copy(db, tables.households, HOUSEHOLD_STAGE_COLUMNS, rows)


def copy_persons(db: Session, tables: StagingTables, rows: list[tuple]) -> int:
    if not rows:
        return 0
    return _copy(db, tables.persons, PERSON_STAGE_COLUMNS, rows)


LOADED_TABLES = ("households", "persons")


def _secondary_index_definitions(db: Session, table_name: str) -> list[str]:
    """Indexes that are not backing a primary key or unique constraint.

    Maintaining these row by row during a multi million row bulk load is far more
    expensive than building them once afterwards, so the load defers them.
    """
    rows = db.execute(
        text(
            """
            SELECT indexdef FROM pg_indexes
            WHERE schemaname = current_schema()
              AND tablename = :table
              AND indexname NOT IN (
                  SELECT conindid::regclass::text FROM pg_constraint
                  WHERE contype IN ('p', 'u') AND conindid IS NOT NULL
              )
            """
        ),
        {"table": table_name},
    ).all()
    return [row[0] for row in rows]


def defer_secondary_indexes(db: Session) -> list[str]:
    definitions: list[str] = []
    for table in LOADED_TABLES:
        for definition in _secondary_index_definitions(db, table):
            name = definition.split(" ON ")[0].split("USING")[0].replace("CREATE INDEX ", "")
            name = name.replace("CREATE UNIQUE INDEX ", "").strip()
            definitions.append(definition)
            db.execute(text(f"DROP INDEX IF EXISTS {name}"))
    db.commit()
    return definitions


def recreate_indexes(db: Session, definitions: list[str]) -> None:
    for definition in definitions:
        db.execute(text(definition))
    db.commit()


def analyse_loaded_tables(db: Session) -> None:
    for table in LOADED_TABLES:
        db.execute(text(f"ANALYZE {table}"))


def count_rows(db: Session, table_name: str) -> int:
    return db.scalar(text(f"SELECT count(*) FROM {table_name}")) or 0


def count_distinct(db: Session, table_name: str, columns: str) -> int:
    return (
        db.scalar(text(f"SELECT count(*) FROM (SELECT DISTINCT {columns} FROM {table_name}) s"))
        or 0
    )


def count_household_conflicts(db: Session, tables: StagingTables) -> int:
    return (
        db.scalar(
            text(
                f"""
                SELECT count(*) FROM (
                    SELECT hhid FROM {tables.households}
                    GROUP BY hhid HAVING count(DISTINCT row_hash) > 1
                ) conflicts
                """
            )
        )
        or 0
    )


def finalise_households(db: Session, survey_id: uuid.UUID, tables: StagingTables) -> int:
    result = db.execute(
        text(
            f"""
            INSERT INTO households (
                id, survey_id, hhid, ml01, ml02, ml03, ml07,
                hh_weight, pop_weight, household_size, attributes
            )
            SELECT gen_random_uuid(), :survey_id, hhid, ml01, ml02, ml03, ml07,
                   hh_weight, pop_weight, NULL, attributes
            FROM (
                SELECT DISTINCT ON (hhid) hhid, ml01, ml02, ml03, ml07,
                       hh_weight, pop_weight, attributes
                FROM {tables.households}
                ORDER BY hhid
            ) unique_households
            ON CONFLICT (survey_id, hhid) DO NOTHING
            """
        ),
        {"survey_id": str(survey_id)},
    )
    return result.rowcount or 0


def finalise_persons(db: Session, survey_id: uuid.UUID, tables: StagingTables) -> int:
    result = db.execute(
        text(
            f"""
            INSERT INTO persons (id, survey_id, household_id, hhid, p01, sex, age, attributes)
            SELECT gen_random_uuid(), :survey_id, h.id, p.hhid, p.p01, p.sex, p.age, p.attributes
            FROM (
                SELECT DISTINCT ON (hhid, p01) hhid, p01, sex, age, attributes
                FROM {tables.persons}
                ORDER BY hhid, p01
            ) p
            JOIN households h ON h.survey_id = :survey_id AND h.hhid = p.hhid
            ON CONFLICT (survey_id, hhid, p01) DO NOTHING
            """
        ),
        {"survey_id": str(survey_id)},
    )
    return result.rowcount or 0


def update_household_sizes(db: Session, survey_id: uuid.UUID) -> None:
    """Count the household size from the loaded persons, after deduplication."""
    db.execute(
        text(
            """
            UPDATE households h
            SET household_size = sizes.person_count
            FROM (
                SELECT household_id, count(*) AS person_count
                FROM persons
                WHERE survey_id = :survey_id AND household_id IS NOT NULL
                GROUP BY household_id
            ) sizes
            WHERE h.id = sizes.household_id
            """
        ),
        {"survey_id": str(survey_id)},
    )
    db.execute(
        text(
            """
            UPDATE households SET household_size = 0
            WHERE survey_id = :survey_id AND household_size IS NULL
            """
        ),
        {"survey_id": str(survey_id)},
    )


HEAD_VARIABLES = ("p02", "p03", "p30a", "p34", "p35", "p36c")


def update_household_heads(
    db: Session,
    survey_id: uuid.UUID,
    *,
    head_code: str | None,
    female_code: str | None,
    education_variable: str = "p30a",
    phone_variable: str = "p36c",
    internet_variable: str = "p35",
) -> int:
    """Copy the household head's attributes onto the household row.

    Every code is resolved from the file value labels by the caller, so nothing is
    assumed about the numbering. When the female code is unknown the column is left
    as NULL rather than guessed.
    """
    if head_code is None:
        return 0

    female_clause = (
        "female_headed = (p.sex = :female_code)" if female_code else "female_headed = NULL"
    )
    parameters: dict[str, Any] = {
        "survey_id": str(survey_id),
        "head_code": head_code,
        "education_variable": education_variable,
        "phone_variable": phone_variable,
        "internet_variable": internet_variable,
    }
    if female_code:
        parameters["female_code"] = female_code

    result = db.execute(
        text(
            f"""
            UPDATE households h
            SET {female_clause},
                head_education_code = p.attributes ->> :education_variable,
                head_phone_type_code = p.attributes ->> :phone_variable,
                head_internet_code = p.attributes ->> :internet_variable
            FROM persons p
            WHERE p.survey_id = :survey_id
              AND p.household_id = h.id
              AND p.attributes ->> 'p02' = :head_code
            """
        ),
        parameters,
    )
    return result.rowcount or 0
