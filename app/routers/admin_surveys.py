"""Administrator survey data endpoints: upload, dictionary, mapping, quality, rollback."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from sqlalchemy import func, select

from app.analysis import cleaning, ingest, mapping, pipeline
from app.analysis.dictionary import load_dictionary
from app.config import get_settings
from app.core.deps import DbSession, client_context, require_permissions
from app.core.errors import not_found, unprocessable
from app.core.pagination import Page, PageParamsDep, build_page
from app.core.permissions import Permission
from app.jobs import queue
from app.models.admin import Admin
from app.models.enums import JobStatus, SurveyStatus
from app.models.survey import JobEvent, Survey, SurveyJob, UploadQualityReport
from app.schemas.common import MessageResponse
from app.schemas.survey import (
    DictionaryEntryRead,
    DictionaryResponse,
    JobDetail,
    JobEventRead,
    JobRead,
    MappingConfirmRequest,
    MappingRowRead,
    MappingTableResponse,
    QualityReportRead,
    SurveyRead,
    SurveyUploadResponse,
    ValueLabelRead,
)
from app.services import audit

router = APIRouter(prefix="/admin", tags=["admin.surveys"])

ReadSurveys = Annotated[Admin, Depends(require_permissions(Permission.SURVEYS_READ))]
WriteSurveys = Annotated[Admin, Depends(require_permissions(Permission.SURVEYS_WRITE))]

SORTABLE = {
    "created_at": Survey.created_at,
    "name": Survey.name,
    "year": Survey.year,
}


def _audit(db, admin: Admin, context: dict, action: str, survey_id, payload: dict) -> None:
    audit.record(
        db,
        action=action,
        entity_type="survey",
        actor_id=admin.id,
        actor_label=admin.email or admin.name,
        entity_id=survey_id,
        payload=payload,
        ip_address=context["ip_address"],
        user_agent=context["user_agent"],
    )


def _get_survey(db: DbSession, survey_id: uuid.UUID) -> Survey:
    survey = db.get(Survey, survey_id)
    if survey is None or survey.deleted_at is not None:
        raise not_found("survey")
    return survey


def _get_job(db: DbSession, job_id: uuid.UUID) -> SurveyJob:
    job = db.get(SurveyJob, job_id)
    if job is None:
        raise not_found("job")
    return job


@router.post(
    "/surveys/upload",
    response_model=SurveyUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload a census .dta file and start the pipeline",
)
def upload_survey(
    db: DbSession,
    admin: WriteSurveys,
    context: Annotated[dict, Depends(client_context)],
    file: Annotated[UploadFile, File(description="Stata .dta file")],
    name: Annotated[str, Form(min_length=2, max_length=200)],
    year: Annotated[int, Form(ge=1970, le=2100)],
    keep_file: Annotated[bool, Form()] = False,
) -> SurveyUploadResponse:
    settings = get_settings()
    survey_id = uuid.uuid4()
    stored = ingest.save_upload(
        file_object=file.file,
        filename=file.filename or "survey.dta",
        settings=settings,
        survey_id=survey_id,
    )

    survey = Survey(
        id=survey_id,
        name=name.strip(),
        year=year,
        original_filename=stored.original_filename,
        stored_path=stored.relative_path,
        file_size_bytes=stored.size_bytes,
        checksum_sha256=stored.checksum,
        status=SurveyStatus.UPLOADED,
        keep_file=keep_file or settings.keep_uploaded_files,
        uploaded_by_id=admin.id,
    )
    db.add(survey)
    job = SurveyJob(survey_id=survey_id, status=JobStatus.QUEUED)
    db.add(job)
    db.flush()
    _audit(
        db,
        admin,
        context,
        "survey.upload",
        survey_id,
        {
            "name": survey.name,
            "year": survey.year,
            "file_size_bytes": survey.file_size_bytes,
            "checksum_sha256": survey.checksum_sha256,
        },
    )
    db.commit()
    db.refresh(survey)
    db.refresh(job)

    pipeline.emit(
        db,
        job,
        pipeline.JobEventType.UPLOAD_RECEIVED,
        stage=pipeline.JobStage.RECEIVED,
        payload={
            "survey_id": str(survey.id),
            "file_size_bytes": survey.file_size_bytes,
            "checksum_sha256": survey.checksum_sha256,
        },
    )
    queue.submit_job(job.id)
    # The job may already have advanced in another session, so read the rows again.
    db.rollback()
    survey = db.get(Survey, survey_id)
    job = db.get(SurveyJob, job.id)
    assert survey is not None and job is not None
    db.refresh(survey)
    db.refresh(job)
    return SurveyUploadResponse(survey=survey, job=job)


@router.get("/surveys", response_model=Page[SurveyRead], summary="List survey uploads")
def list_surveys(
    params: PageParamsDep,
    db: DbSession,
    admin: ReadSurveys,
    status_filter: Annotated[SurveyStatus | None, Query(alias="status")] = None,
    year: Annotated[int | None, Query(ge=1970, le=2100)] = None,
    q: Annotated[str | None, Query(max_length=120)] = None,
) -> dict:
    query = select(Survey).where(Survey.deleted_at.is_(None))
    if status_filter is not None:
        query = query.where(Survey.status == status_filter)
    if year is not None:
        query = query.where(Survey.year == year)
    if q:
        query = query.where(func.lower(Survey.name).like(f"%{q.strip().lower()}%"))

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    column = SORTABLE.get(params.sort or "created_at", Survey.created_at)
    ordering = column.asc() if params.order == "asc" else column.desc()
    rows = db.scalars(query.order_by(ordering).offset(params.offset).limit(params.page_size))
    return build_page([SurveyRead.model_validate(row) for row in rows], total, params)


@router.get("/surveys/{survey_id}", response_model=SurveyRead, summary="Read one survey")
def get_survey(survey_id: uuid.UUID, db: DbSession, admin: ReadSurveys) -> SurveyRead:
    return SurveyRead.model_validate(_get_survey(db, survey_id))


@router.delete(
    "/surveys/{survey_id}",
    response_model=MessageResponse,
    summary="Roll back an upload and delete its rows",
)
def delete_survey(
    survey_id: uuid.UUID,
    db: DbSession,
    admin: WriteSurveys,
    context: Annotated[dict, Depends(client_context)],
) -> MessageResponse:
    settings = get_settings()
    survey = _get_survey(db, survey_id)

    cleaning.clear_survey_data(db, survey.id)
    quality = db.scalar(
        select(UploadQualityReport).where(UploadQualityReport.survey_id == survey.id)
    )
    if quality is not None:
        db.delete(quality)
    survey.status = SurveyStatus.DELETED
    survey.deleted_at = datetime.now(UTC)
    _audit(db, admin, context, "survey.delete", survey.id, {"name": survey.name})
    db.commit()

    ingest.delete_stored_file(settings, survey.stored_path)
    return MessageResponse(message="survey_deleted")


@router.get(
    "/surveys/{survey_id}/dictionary",
    response_model=DictionaryResponse,
    summary="Browse the questionnaire dictionary",
)
def get_dictionary(survey_id: uuid.UUID, db: DbSession, admin: ReadSurveys) -> DictionaryResponse:
    _get_survey(db, survey_id)
    entries = load_dictionary(db, survey_id)
    variables = [
        DictionaryEntryRead(
            variable_name=entry.variable_name,
            label=entry.label,
            section=entry.section,
            dtype=entry.dtype,
            position=entry.position,
            value_labels=[
                ValueLabelRead(
                    value_text=item.value,
                    label=item.label,
                    is_yes=item.is_yes,
                    is_missing=item.is_missing,
                )
                for item in entry.value_labels
            ],
        )
        for entry in entries.values()
    ]
    return DictionaryResponse(survey_id=survey_id, variables=variables)


@router.get(
    "/surveys/{survey_id}/quality-report",
    response_model=QualityReportRead,
    summary="Read the data quality report",
)
def get_quality_report(
    survey_id: uuid.UUID, db: DbSession, admin: ReadSurveys
) -> QualityReportRead:
    _get_survey(db, survey_id)
    quality = db.scalar(
        select(UploadQualityReport).where(UploadQualityReport.survey_id == survey_id)
    )
    if quality is None:
        raise not_found("quality_report")
    return QualityReportRead.model_validate(quality)


@router.get("/jobs/{job_id}", response_model=JobDetail, summary="Read a job with its events")
def get_job(job_id: uuid.UUID, db: DbSession, admin: ReadSurveys) -> JobDetail:
    job = _get_job(db, job_id)
    events = db.scalars(
        select(JobEvent).where(JobEvent.job_id == job.id).order_by(JobEvent.created_at).limit(200)
    ).all()
    detail = JobDetail.model_validate(job)
    detail.events = [JobEventRead.model_validate(event) for event in events]
    return detail


@router.get(
    "/jobs/{job_id}/mapping",
    response_model=MappingTableResponse,
    summary="Read the proposed field mapping",
)
def get_mapping(job_id: uuid.UUID, db: DbSession, admin: ReadSurveys) -> MappingTableResponse:
    job = _get_job(db, job_id)
    survey = _get_survey(db, job.survey_id)
    rows = mapping.mapping_table(db, survey.id)
    return MappingTableResponse(
        survey_id=survey.id,
        confirmed=survey.mapping_confirmed_at is not None,
        variables=[MappingRowRead.model_validate(row) for row in rows],
    )


@router.post(
    "/jobs/{job_id}/mapping",
    response_model=JobRead,
    summary="Confirm the mapping and yes codes, then resume the job",
)
def confirm_mapping(
    job_id: uuid.UUID,
    payload: MappingConfirmRequest,
    db: DbSession,
    admin: WriteSurveys,
    context: Annotated[dict, Depends(client_context)],
) -> JobRead:
    job = _get_job(db, job_id)
    survey = _get_survey(db, job.survey_id)
    if job.status == JobStatus.COMPLETED:
        raise unprocessable(
            "job.already_completed",
            "errors.job.already_completed",
            "This job has already completed",
        )

    mapping.confirm_mappings(
        db, survey, admin.id, [update.model_dump() for update in payload.updates]
    )
    _audit(
        db,
        admin,
        context,
        "survey.mapping_confirm",
        survey.id,
        {"job_id": str(job.id), "updates": len(payload.updates)},
    )
    db.commit()

    job.status = JobStatus.QUEUED
    db.commit()
    queue.submit_job(job.id)
    db.rollback()
    refreshed = db.get(SurveyJob, job_id)
    assert refreshed is not None
    db.refresh(refreshed)
    return JobRead.model_validate(refreshed)
