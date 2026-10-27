"""Plantings, task instances, notifications, SMS log, harvests and support requests."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, value_enum
from app.models.enums import (
    FundingStatus,
    HarvestUse,
    NotificationType,
    PlantingStatus,
    SmsStatus,
    SpecialistRequestStatus,
    SpecialistType,
    TaskInstanceStatus,
    TaskType,
    TimeOfDay,
)


class Planting(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "plantings"
    __table_args__ = (Index("ix_plantings_user_status", "user_id", "status"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Plantings keep the version they started on unless the admin applies an update.
    product_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[PlantingStatus] = mapped_column(
        value_enum(PlantingStatus, "planting_status", 16),
        nullable=False,
        default=PlantingStatus.ACTIVE,
        server_default=PlantingStatus.ACTIVE.value,
        index=True,
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    started_on: Mapped[date] = mapped_column(Date, nullable=False)
    expected_harvest_on: Mapped[date | None] = mapped_column(Date)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    checklist_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )

    tasks: Mapped[list[TaskInstance]] = relationship(
        back_populates="planting", cascade="all, delete-orphan"
    )


class TaskInstance(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "task_instances"
    __table_args__ = (
        Index("ix_task_instances_user_due", "user_id", "due_at"),
        Index("ix_task_instances_planting_due", "planting_id", "due_at"),
        Index("ix_task_instances_status_due", "status", "due_at"),
    )

    planting_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("plantings.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_schedule_task_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("product_schedule_tasks.id", ondelete="SET NULL")
    )
    task_type: Mapped[TaskType] = mapped_column(
        value_enum(TaskType, "task_type", 24), nullable=False
    )
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    time_of_day: Mapped[TimeOfDay] = mapped_column(
        value_enum(TimeOfDay, "time_of_day", 16),
        nullable=False,
        default=TimeOfDay.MORNING,
        server_default=TimeOfDay.MORNING.value,
    )
    instruction_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[TaskInstanceStatus] = mapped_column(
        value_enum(TaskInstanceStatus, "task_instance_status", 20),
        nullable=False,
        default=TaskInstanceStatus.PENDING,
        server_default=TaskInstanceStatus.PENDING.value,
        index=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    skipped_reason: Mapped[str | None] = mapped_column(String(500))
    reminder_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    follow_up_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_urgent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    planting: Mapped[Planting] = relationship(back_populates="tasks")


class Notification(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_user_created", "user_id", "created_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[NotificationType] = mapped_column(
        value_enum(NotificationType, "notification_type", 32), nullable=False
    )
    title_translation_key: Mapped[str] = mapped_column(String(120), nullable=False)
    body_translation_key: Mapped[str] = mapped_column(String(120), nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    task_instance_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("task_instances.id", ondelete="SET NULL")
    )
    sms_sent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SmsLog(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "sms_log"
    __table_args__ = (Index("ix_sms_log_status_created", "status", "created_at"),)

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    notification_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("notifications.id", ondelete="SET NULL")
    )
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    template_key: Mapped[str | None] = mapped_column(String(120))
    language: Mapped[str | None] = mapped_column(String(4))
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[SmsStatus] = mapped_column(
        value_enum(SmsStatus, "sms_status", 16),
        nullable=False,
        default=SmsStatus.QUEUED,
        server_default=SmsStatus.QUEUED.value,
        index=True,
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    provider_message_id: Mapped[str | None] = mapped_column(String(120))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Harvest(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "harvests"
    __table_args__ = (Index("ix_harvests_planting_date", "planting_id", "harvested_on"),)

    planting_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("plantings.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    harvested_on: Mapped[date] = mapped_column(Date, nullable=False)
    quantity_kg: Mapped[float | None] = mapped_column(Numeric(12, 3))
    quantity_note: Mapped[str | None] = mapped_column(String(200))
    used_for: Mapped[HarvestUse] = mapped_column(
        value_enum(HarvestUse, "harvest_use", 16), nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SpecialistRequest(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "specialist_requests"
    __table_args__ = (Index("ix_specialist_requests_status", "status", "created_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("products.id", ondelete="SET NULL")
    )
    specialist_type: Mapped[SpecialistType] = mapped_column(
        value_enum(SpecialistType, "specialist_type", 32), nullable=False
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    photo_url: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[SpecialistRequestStatus] = mapped_column(
        value_enum(SpecialistRequestStatus, "specialist_request_status", 16),
        nullable=False,
        default=SpecialistRequestStatus.NEW,
        server_default=SpecialistRequestStatus.NEW.value,
        index=True,
    )
    assigned_to: Mapped[str | None] = mapped_column(String(160))
    admin_notes: Mapped[str | None] = mapped_column(Text)
    contacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FundingInterest(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "funding_interest"
    __table_args__ = (Index("ix_funding_interest_status", "status", "created_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("products.id", ondelete="SET NULL")
    )
    planned_size: Mapped[str] = mapped_column(String(200), nullable=False)
    location: Mapped[str] = mapped_column(String(200), nullable=False)
    building_or_land: Mapped[str] = mapped_column(String(200), nullable=False)
    experience: Mapped[str | None] = mapped_column(Text)
    funding_needed: Mapped[float | None] = mapped_column(Numeric(14, 2))
    consent_to_share: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[FundingStatus] = mapped_column(
        value_enum(FundingStatus, "funding_status", 20),
        nullable=False,
        default=FundingStatus.NEW,
        server_default=FundingStatus.NEW.value,
        index=True,
    )
    referred_to_brd_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    brd_reference: Mapped[str | None] = mapped_column(String(120))
    admin_notes: Mapped[str | None] = mapped_column(Text)


class MessageTemplate(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "message_templates"
    __table_args__ = (
        UniqueConstraint("key", "language", name="uq_message_templates_key_language"),
    )

    key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    language: Mapped[str] = mapped_column(String(4), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    sms_body: Mapped[str | None] = mapped_column(Text)
    variables: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
