"""Pipeline orchestration: stages, job state and persisted events.

Milestone 2 runs stages 1 to 5 (received, validated, mapped, cleaned, loaded).
Aggregation and scoring are appended in later milestones without changing the
shape of the job row or the event stream.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.analysis import aggregation, cleaning, loading, mapping
from app.analysis.dictionary import (
    DictionaryEntry,
    code_for_keywords,
    load_dictionary,
    store_dictionary,
)
from app.analysis.ingest import upload_directory, validate_upload
from app.config import Settings, get_settings
from app.db import get_session_factory
from app.models.enums import JobEventType, JobStage, JobStatus, SurveyStatus
from app.models.survey import JobEvent, Survey, SurveyJob, UploadQualityReport

logger = logging.getLogger("agura.analysis.pipeline")

STAGES: tuple[JobStage, ...] = (
    JobStage.RECEIVED,
    JobStage.VALIDATED,
    JobStage.MAPPED,
    JobStage.CLEANED,
    JobStage.LOADED,
    JobStage.AGGREGATED,
)

STAGE_WEIGHT: dict[JobStage, int] = {
    JobStage.RECEIVED: 5,
    JobStage.VALIDATED: 15,
    JobStage.MAPPED: 10,
    JobStage.CLEANED: 45,
    JobStage.LOADED: 15,
    JobStage.AGGREGATED: 10,
}

ADVISORY_LOCK_NAMESPACE = 4_812_003


class PipelineError(Exception):
    """An expected failure with a stable code the clients translate."""

    def __init__(self, code: str, message: str, stage: JobStage | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.stage = stage

    @property
    def translation_key(self) -> str:
        return f"errors.{self.code}"


def _now() -> datetime:
    return datetime.now(UTC)


def _stage_index(stage: JobStage) -> int:
    try:
        return STAGES.index(stage)
    except ValueError:
        return 0


def _base_percent(stage: JobStage) -> int:
    return sum(STAGE_WEIGHT[item] for item in STAGES[: _stage_index(stage)])


def emit(
    db: Session,
    job: SurveyJob,
    event_type: JobEventType,
    *,
    stage: JobStage | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    db.add(
        JobEvent(
            job_id=job.id,
            survey_id=job.survey_id,
            type=event_type.value,
            stage=(stage or job.stage).value if (stage or job.stage) else None,
            payload=payload or {},
        )
    )
    db.commit()


def staging_usable(db: Session, job: SurveyJob) -> bool:
    """Unlogged staging tables are emptied by a restart, so presence is not enough."""
    tables = loading.staging_tables_for(job.id.hex)
    found = db.scalar(
        text(
            """
            SELECT count(*) FROM information_schema.tables
            WHERE table_name IN (:households, :persons)
            """
        ),
        {"households": tables.households, "persons": tables.persons},
    )
    if not found or int(found) != 2:
        return False
    try:
        return (db.scalar(text(f"SELECT count(*) FROM {tables.households}")) or 0) > 0
    except Exception:
        db.rollback()
        return False


def _file_checksum(path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _stage_received(
    db: Session, job: SurveyJob, survey: Survey, settings: Settings
) -> dict[str, Any]:
    path = upload_directory(settings) / survey.stored_path
    if not path.exists():
        raise PipelineError(
            "survey.file_missing",
            "The uploaded file is no longer on disk",
            JobStage.RECEIVED,
        )
    checksum = _file_checksum(path)
    if checksum != survey.checksum_sha256:
        raise PipelineError(
            "survey.checksum_mismatch",
            "The stored file does not match the checksum recorded at upload",
            JobStage.RECEIVED,
        )
    return {"file_size_bytes": survey.file_size_bytes, "checksum_sha256": checksum}


def _stage_validated(
    db: Session, job: SurveyJob, survey: Survey, settings: Settings
) -> tuple[dict[str, Any], list[DictionaryEntry]]:
    path = upload_directory(settings) / survey.stored_path
    result = validate_upload(path, settings.required_survey_variables, settings.dta_encoding)

    if result.missing_key_variables:
        raise PipelineError(
            "survey.missing_key_variables",
            "Missing key variables: " + ", ".join(result.missing_key_variables),
            JobStage.VALIDATED,
        )
    if not result.entries:
        raise PipelineError(
            "survey.empty_dictionary",
            "The file has no variables",
            JobStage.VALIDATED,
        )

    store_dictionary(db, survey.id, result.entries)
    survey.rows_raw = result.row_count
    db.commit()
    summary = {
        "variables": result.column_count,
        "rows_in_file": result.row_count,
        "key_variables_present": result.key_variables_present,
    }
    return summary, result.entries


def _stage_mapped(
    db: Session, job: SurveyJob, survey: Survey, entries: list[DictionaryEntry] | None
) -> tuple[dict[str, Any], bool]:
    dictionary_entries = entries or list(load_dictionary(db, survey.id).values())
    existing = mapping.mapping_table(db, survey.id)
    if not existing:
        mapping.propose_mappings(db, survey.id, dictionary_entries)
        db.commit()

    rows = mapping.mapping_table(db, survey.id)
    detected = {row.variable_name: row.yes_code for row in rows if row.yes_code is not None}
    summary = {
        "variables": len(rows),
        "yes_codes_detected": detected,
        "awaiting_confirmation": survey.mapping_confirmed_at is None,
    }
    if survey.mapping_confirmed_at is None:
        return summary, False
    return summary, True


def _stage_cleaned(
    db: Session,
    job: SurveyJob,
    survey: Survey,
    settings: Settings,
    on_progress,
) -> cleaning.StagedCleaning:
    dictionary = load_dictionary(db, survey.id)
    path = upload_directory(settings) / survey.stored_path
    return cleaning.run_cleaning(
        db=db,
        survey_id=survey.id,
        job_id=job.id,
        path=str(path),
        dictionary=dictionary,
        chunk_size=settings.analysis_chunk_size,
        encoding=settings.dta_encoding,
        total_rows=survey.rows_raw,
        on_progress=on_progress,
    )


HEAD_KEYWORDS = {"head", "household head", "umukuru w urugo", "chief"}
FEMALE_KEYWORDS = {"female", "umugore", "femme"}


def _stage_loaded(
    db: Session, job: SurveyJob, survey: Survey, staged: cleaning.StagedCleaning
) -> dict[str, Any]:
    result = cleaning.run_loading(db, survey.id, staged)

    dictionary = load_dictionary(db, survey.id)
    head_code = code_for_keywords(dictionary.get("p02"), HEAD_KEYWORDS)
    female_code = code_for_keywords(dictionary.get("p03"), FEMALE_KEYWORDS)
    heads = loading.update_household_heads(
        db,
        survey.id,
        head_code=head_code,
        female_code=female_code,
    )
    db.commit()

    report = db.scalar(
        select(UploadQualityReport).where(UploadQualityReport.survey_id == survey.id)
    )
    if report is None:
        report = UploadQualityReport(survey_id=survey.id)
        db.add(report)
    report.rows_raw = result.rows_raw
    report.rows_after_dedup = result.rows_after_dedup
    report.exact_duplicate_rows = result.exact_duplicate_rows
    report.household_rows_read = result.household_rows_read
    report.duplicate_households = result.duplicate_households
    report.duplicate_persons = result.duplicate_persons
    report.households_loaded = result.households_loaded
    report.persons_loaded = result.persons_loaded
    report.household_variable_conflicts = result.household_variable_conflicts
    report.rule_violations = result.rule_violations
    report.missing_rates = result.missing_rates
    report.warnings = result.warnings
    db.commit()

    return {
        "rows_raw": result.rows_raw,
        "rows_after_dedup": result.rows_after_dedup,
        "exact_duplicate_rows": result.exact_duplicate_rows,
        "duplicate_households": result.duplicate_households,
        "duplicate_persons": result.duplicate_persons,
        "household_variable_conflicts": result.household_variable_conflicts,
        "household_rows_read": result.household_rows_read,
        "households_loaded": result.households_loaded,
        "persons_loaded": result.persons_loaded,
        "households_with_head_identified": heads,
        "warnings": result.warnings,
    }


def _fail(
    db: Session,
    job: SurveyJob,
    survey: Survey | None,
    code: str,
    message: str,
    translation_key: str,
    stage: JobStage | None,
) -> None:
    db.rollback()
    if survey is not None:
        cleaning.clear_survey_data(db, survey.id)
        survey.status = SurveyStatus.FAILED
    job.status = JobStatus.FAILED
    job.error_code = code
    job.error_message = message
    job.error_translation_key = translation_key
    job.error_stage = (stage or job.stage).value
    job.finished_at = _now()
    db.commit()
    emit(
        db,
        job,
        JobEventType.JOB_FAILED,
        stage=stage,
        payload={"code": code, "message": message, "stage": (stage or job.stage).value},
    )
    logger.warning("Survey job %s failed at %s: %s", job.id, code, message)


def _execute(db: Session, job: SurveyJob, settings: Settings) -> None:
    survey = db.get(Survey, job.survey_id)
    if survey is None or survey.deleted_at is not None:
        logger.info("Job %s has no active survey, nothing to do", job.id)
        return
    if job.status in {JobStatus.COMPLETED, JobStatus.CANCELLED}:
        return

    if job.stage == JobStage.LOADED and not staging_usable(db, job):
        job.stage = JobStage.CLEANED
        db.commit()

    job.status = JobStatus.RUNNING
    job.started_at = job.started_at or _now()
    job.error_code = None
    job.error_message = None
    job.error_translation_key = None
    job.error_stage = None
    survey.status = SurveyStatus.PROCESSING
    db.commit()
    emit(db, job, JobEventType.JOB_STARTED, stage=job.stage, payload={"survey_id": str(survey.id)})

    start_index = _stage_index(job.stage)
    entries: list[DictionaryEntry] | None = list(load_dictionary(db, survey.id).values()) or None
    staged: cleaning.StagedCleaning | None = None
    progress_state = {"last": -1.0}

    for position, stage in enumerate(STAGES):
        if position < start_index:
            continue

        job.stage = stage
        db.commit()
        emit(db, job, JobEventType.JOB_STAGE_STARTED, stage=stage, payload={})

        if stage is JobStage.RECEIVED:
            summary = _stage_received(db, job, survey, settings)
            job.summary = {**job.summary, "received": summary}

        elif stage is JobStage.VALIDATED:
            summary, entries = _stage_validated(db, job, survey, settings)
            job.summary = {**job.summary, "validated": summary}

        elif stage is JobStage.MAPPED:
            summary, confirmed = _stage_mapped(db, job, survey, entries)
            job.summary = {**job.summary, "mapped": summary}
            db.commit()
            if not confirmed:
                job.status = JobStatus.AWAITING_MAPPING
                job.progress_percent = _base_percent(JobStage.MAPPED)
                survey.status = SurveyStatus.AWAITING_MAPPING
                db.commit()
                emit(
                    db,
                    job,
                    JobEventType.JOB_MAPPING_REQUIRED,
                    stage=JobStage.MAPPED,
                    payload={"variables": summary["variables"]},
                )
                return

        elif stage is JobStage.CLEANED:

            def on_progress(rows: int, percent: float, cleaning_stage: JobStage = stage) -> None:
                job.rows_processed = rows
                job.progress_percent = min(
                    99,
                    _base_percent(JobStage.CLEANED)
                    + int(percent * STAGE_WEIGHT[cleaning_stage] / 100),
                )
                db.commit()
                if percent - progress_state["last"] >= 5:
                    progress_state["last"] = percent
                    emit(
                        db,
                        job,
                        JobEventType.JOB_STAGE_PROGRESS,
                        stage=cleaning_stage,
                        payload={
                            "percent": round(percent, 1),
                            "rows_processed": rows,
                            "stage": cleaning_stage.value,
                        },
                    )

            staged = _stage_cleaned(db, job, survey, settings, on_progress)
            job.summary = {
                **job.summary,
                "cleaned": {
                    "rows_read": staged.stats.rows_raw,
                    "exact_duplicate_rows": staged.stats.exact_duplicate_rows,
                    "duplicate_households": staged.stats.duplicate_households,
                    "duplicate_persons": staged.stats.duplicate_persons,
                    "household_variable_conflicts": staged.conflicts,
                },
            }

        elif stage is JobStage.LOADED:
            if staged is None:
                raise PipelineError(
                    "job.staging_missing",
                    "Cleaned rows are no longer available, run the cleaned stage again",
                    JobStage.LOADED,
                )
            summary = _stage_loaded(db, job, survey, staged)
            job.summary = {**job.summary, "loaded": summary}

        elif stage is JobStage.AGGREGATED:
            aggregation_result = aggregation.run_aggregation(db, survey)
            summary = aggregation_result.as_summary()
            job.summary = {**job.summary, "aggregated": summary}
            db.commit()

        job.progress_percent = _base_percent(stage) + STAGE_WEIGHT[stage]
        db.commit()
        emit(
            db,
            job,
            JobEventType.JOB_STAGE_COMPLETED,
            stage=stage,
            payload=_stage_summary_for_event(stage, job.summary),
        )

    survey.status = SurveyStatus.READY
    job.status = JobStatus.COMPLETED
    job.progress_percent = 100
    job.finished_at = _now()
    db.commit()
    emit(
        db,
        job,
        JobEventType.JOB_COMPLETED,
        stage=JobStage.LOADED,
        payload=job.summary.get("loaded", {}),
    )


def _stage_summary_for_event(stage: JobStage, summary: dict[str, Any]) -> dict[str, Any]:
    return summary.get(stage.value, {})


def run_job(job_id: uuid.UUID, settings: Settings | None = None) -> None:
    """Run or resume one survey job. Safe to call from a thread or another process."""
    settings = settings or get_settings()
    session = get_session_factory()()
    lock_key = ADVISORY_LOCK_NAMESPACE + (job_id.int % 1_000_000)
    acquired = False
    try:
        acquired = bool(
            session.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_key})
        )
        if not acquired:
            logger.info("Job %s is already running elsewhere", job_id)
            return

        job = session.get(SurveyJob, job_id)
        if job is None:
            logger.warning("Job %s does not exist", job_id)
            return

        try:
            _execute(session, job, settings)
        except PipelineError as exc:
            _fail(
                session,
                job,
                session.get(Survey, job.survey_id),
                exc.code,
                exc.message,
                exc.translation_key,
                exc.stage,
            )
        except Exception as exc:  # noqa: BLE001 - the job row must always record the failure
            logger.exception("Unexpected failure in job %s", job_id)
            _fail(
                session,
                job,
                session.get(Survey, job.survey_id),
                "job.unexpected_error",
                f"{type(exc).__name__}: {exc}"[:500],
                "errors.job.unexpected_error",
                job.stage,
            )
    finally:
        if acquired:
            try:
                session.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key})
                session.commit()
            except Exception:  # pragma: no cover
                session.rollback()
        session.close()
