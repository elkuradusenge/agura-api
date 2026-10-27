"""Survey uploads, pipeline jobs and the questionnaire dictionary."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, value_enum
from app.models.enums import JobStage, JobStatus, PlatformGroup, SurveyStatus


class Survey(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "surveys"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(500), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[SurveyStatus] = mapped_column(
        value_enum(SurveyStatus, "survey_status", 20),
        nullable=False,
        default=SurveyStatus.UPLOADED,
        server_default=SurveyStatus.UPLOADED.value,
        index=True,
    )
    keep_file: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("admins.id", ondelete="SET NULL")
    )
    rows_raw: Mapped[int | None] = mapped_column(Integer)
    mapping_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    jobs: Mapped[list[SurveyJob]] = relationship(
        back_populates="survey", cascade="all, delete-orphan", order_by="SurveyJob.created_at"
    )
    quality_report: Mapped[UploadQualityReport | None] = relationship(
        back_populates="survey", cascade="all, delete-orphan", uselist=False
    )


class SurveyJob(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "survey_jobs"
    __table_args__ = (Index("ix_survey_jobs_status_created", "status", "created_at"),)

    survey_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("surveys.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[JobStatus] = mapped_column(
        value_enum(JobStatus, "job_status", 20),
        nullable=False,
        default=JobStatus.QUEUED,
        server_default=JobStatus.QUEUED.value,
    )
    stage: Mapped[JobStage] = mapped_column(
        value_enum(JobStage, "job_stage", 20),
        nullable=False,
        default=JobStage.RECEIVED,
        server_default=JobStage.RECEIVED.value,
    )
    progress_percent: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    rows_processed: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    error_translation_key: Mapped[str | None] = mapped_column(String(120))
    error_stage: Mapped[str | None] = mapped_column(String(20))
    summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    survey: Mapped[Survey] = relationship(back_populates="jobs")


class JobEvent(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "job_events"
    __table_args__ = (Index("ix_job_events_job_created", "job_id", "created_at"),)

    job_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("survey_jobs.id", ondelete="CASCADE"), nullable=False
    )
    survey_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("surveys.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(60), nullable=False)
    stage: Mapped[str | None] = mapped_column(String(30))
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class VariableDictionary(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "variable_dictionary"
    __table_args__ = (
        UniqueConstraint("survey_id", "variable_name", name="uq_dictionary_variable"),
    )

    survey_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("surveys.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    variable_name: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str | None] = mapped_column(Text)
    section: Mapped[str | None] = mapped_column(String(40))
    dtype: Mapped[str | None] = mapped_column(String(30))
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    value_labels: Mapped[list[ValueLabel]] = relationship(
        back_populates="dictionary_entry", cascade="all, delete-orphan"
    )


class ValueLabel(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "value_labels"
    __table_args__ = (
        UniqueConstraint("dictionary_id", "value_text", name="uq_value_labels_entry_value"),
    )

    dictionary_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("variable_dictionary.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    value_text: Mapped[str] = mapped_column(String(30), nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    is_yes: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    is_missing: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    dictionary_entry: Mapped[VariableDictionary] = relationship(back_populates="value_labels")


class FieldMapping(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "field_mappings"
    __table_args__ = (UniqueConstraint("survey_id", "variable_name", name="uq_mapping_variable"),)

    survey_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("surveys.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    variable_name: Mapped[str] = mapped_column(String(64), nullable=False)
    platform_group: Mapped[PlatformGroup] = mapped_column(
        value_enum(PlatformGroup, "platform_group", 32),
        nullable=False,
        default=PlatformGroup.UNKNOWN,
        server_default=PlatformGroup.UNKNOWN.value,
    )
    yes_code: Mapped[str | None] = mapped_column(String(20))
    yes_label: Mapped[str | None] = mapped_column(String(120))
    confirmed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    confirmed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("admins.id", ondelete="SET NULL")
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UploadQualityReport(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "upload_quality_report"

    survey_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("surveys.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    rows_raw: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    rows_after_dedup: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    exact_duplicate_rows: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    household_rows_read: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    duplicate_households: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    duplicate_persons: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    households_loaded: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    persons_loaded: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    household_variable_conflicts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    rule_violations: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    missing_rates: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    warnings: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    survey: Mapped[Survey] = relationship(back_populates="quality_report")
