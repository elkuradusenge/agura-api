"""Cleaned household records and the food reference table."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin, value_enum
from app.models.enums import FoodGroup


class Household(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "households"
    __table_args__ = (
        UniqueConstraint("survey_id", "hhid", name="uq_households_survey_hhid"),
        Index("ix_households_survey_province", "survey_id", "ml01"),
        Index("ix_households_survey_district", "survey_id", "ml02"),
        Index("ix_households_survey_sector", "survey_id", "ml03"),
    )

    survey_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("surveys.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    hhid: Mapped[str] = mapped_column(String(40), nullable=False)
    ml01: Mapped[str | None] = mapped_column(String(120))
    ml02: Mapped[str | None] = mapped_column(String(120))
    ml03: Mapped[str | None] = mapped_column(String(120))
    ml07: Mapped[str | None] = mapped_column(String(40))
    hh_weight: Mapped[float | None] = mapped_column(Numeric(18, 4))
    pop_weight: Mapped[float | None] = mapped_column(Numeric(18, 4))
    household_size: Mapped[int | None] = mapped_column(Integer)
    food_diversity_count: Mapped[int | None] = mapped_column(Integer)
    female_headed: Mapped[bool | None] = mapped_column(Boolean)
    head_education_code: Mapped[str | None] = mapped_column(String(20))
    head_phone_type_code: Mapped[str | None] = mapped_column(String(20))
    head_internet_code: Mapped[str | None] = mapped_column(String(20))
    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class FoodItem(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "food_items"

    standard_name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    local_name: Mapped[str | None] = mapped_column(String(120))
    group: Mapped[FoodGroup] = mapped_column(
        value_enum(FoodGroup, "food_group", 20), nullable=False, index=True
    )
    source_variable: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    count_variable: Mapped[str | None] = mapped_column(String(64))
    nutrition_group: Mapped[str | None] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
