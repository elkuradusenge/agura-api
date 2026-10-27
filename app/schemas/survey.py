"""Survey upload, job and dictionary schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import JobStage, JobStatus, PlatformGroup, SurveyStatus


class SurveyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    year: int
    original_filename: str
    file_size_bytes: int
    checksum_sha256: str
    status: SurveyStatus
    keep_file: bool
    rows_raw: int | None = None
    mapping_confirmed_at: datetime | None = None
    uploaded_by_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime


class JobEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: str
    stage: str | None = None
    payload: dict[str, Any]
    created_at: datetime


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    survey_id: uuid.UUID
    status: JobStatus
    stage: JobStage
    progress_percent: int
    rows_processed: int
    error_code: str | None = None
    error_message: str | None = None
    error_translation_key: str | None = None
    error_stage: str | None = None
    summary: dict[str, Any]
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime


class JobDetail(JobRead):
    events: list[JobEventRead] = []


class SurveyUploadResponse(BaseModel):
    survey: SurveyRead
    job: JobRead


class ValueLabelRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    value_text: str
    label: str
    is_yes: bool
    is_missing: bool


class DictionaryEntryRead(BaseModel):
    variable_name: str
    label: str | None = None
    section: str | None = None
    dtype: str | None = None
    position: int
    value_labels: list[ValueLabelRead] = []


class DictionaryResponse(BaseModel):
    survey_id: uuid.UUID
    variables: list[DictionaryEntryRead]


class MappingRowRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    variable_name: str
    platform_group: PlatformGroup
    yes_code: str | None = None
    yes_label: str | None = None
    confirmed: bool


class MappingTableResponse(BaseModel):
    survey_id: uuid.UUID
    confirmed: bool
    variables: list[MappingRowRead]


class MappingUpdate(BaseModel):
    variable_name: str = Field(min_length=1, max_length=64)
    platform_group: PlatformGroup | None = None
    yes_code: str | None = Field(default=None, max_length=20)


class MappingConfirmRequest(BaseModel):
    updates: list[MappingUpdate] = []


class QualityReportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    survey_id: uuid.UUID
    rows_raw: int
    rows_after_dedup: int
    exact_duplicate_rows: int
    household_rows_read: int
    duplicate_households: int
    duplicate_persons: int
    households_loaded: int
    persons_loaded: int
    household_variable_conflicts: int
    rule_violations: list[dict[str, Any]]
    missing_rates: dict[str, Any]
    warnings: list[str]
    created_at: datetime
