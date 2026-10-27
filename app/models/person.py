"""Cleaned person records."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin


class Person(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "persons"
    __table_args__ = (
        UniqueConstraint("survey_id", "hhid", "p01", name="uq_persons_survey_hhid_p01"),
        Index("ix_persons_survey_household", "survey_id", "household_id"),
    )

    survey_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("surveys.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    household_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE")
    )
    hhid: Mapped[str] = mapped_column(String(40), nullable=False)
    p01: Mapped[str] = mapped_column(String(40), nullable=False)
    sex: Mapped[str | None] = mapped_column(String(20))
    age: Mapped[int | None] = mapped_column(Integer)
    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
