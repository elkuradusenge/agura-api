"""Aggregate tables produced by the analysis pipeline.

Raw counts and weighted sums are stored exactly as computed. Small cell suppression
is applied when a response is built, so changing the threshold in settings takes
effect immediately without re-running the pipeline.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
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
from app.models.enums import GeoLevel


class ProvinceFoodSummary(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "province_food_summary"
    __table_args__ = (
        UniqueConstraint(
            "survey_id",
            "food_item_id",
            "level",
            "province",
            "district",
            "sector",
            name="uq_province_food_summary_cell",
        ),
        Index("ix_province_food_summary_survey_level", "survey_id", "level"),
        Index("ix_province_food_summary_food", "food_item_id"),
        Index("ix_province_food_summary_geo", "province", "district", "sector"),
    )

    survey_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("surveys.id", ondelete="CASCADE"), nullable=False
    )
    food_item_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("food_items.id", ondelete="CASCADE"), nullable=False
    )
    level: Mapped[GeoLevel] = mapped_column(value_enum(GeoLevel, "geo_level", 16), nullable=False)
    province: Mapped[str | None] = mapped_column(String(120))
    district: Mapped[str | None] = mapped_column(String(120))
    sector: Mapped[str | None] = mapped_column(String(120))

    households_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    weighted_households_total: Mapped[float] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )
    households_with_food: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    weighted_households_with_food: Mapped[float] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )

    average_count: Mapped[float | None] = mapped_column(Numeric(14, 4))
    average_count_basis: Mapped[int | None] = mapped_column(Integer)
    rearing_district_distribution: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MushroomSummary(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "mushroom_summary"
    __table_args__ = (
        UniqueConstraint(
            "survey_id",
            "level",
            "province",
            "district",
            "sector",
            name="uq_mushroom_summary_cell",
        ),
        Index("ix_mushroom_summary_survey_level", "survey_id", "level"),
        Index("ix_mushroom_summary_geo", "province", "district", "sector"),
    )

    survey_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("surveys.id", ondelete="CASCADE"), nullable=False
    )
    level: Mapped[GeoLevel] = mapped_column(value_enum(GeoLevel, "geo_level", 16), nullable=False)
    province: Mapped[str | None] = mapped_column(String(120))
    district: Mapped[str | None] = mapped_column(String(120))
    sector: Mapped[str | None] = mapped_column(String(120))

    households_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    weighted_households_total: Mapped[float] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )
    mushroom_households: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    weighted_mushroom_households: Mapped[float] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )

    urban_mushroom_households: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rural_mushroom_households: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    weighted_urban_mushroom: Mapped[float] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )
    weighted_rural_mushroom: Mapped[float] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )

    female_headed_mushroom_households: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    weighted_female_headed_mushroom: Mapped[float] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )
    households_with_heads_known: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    average_household_size: Mapped[float | None] = mapped_column(Numeric(8, 3))
    mushroom_average_household_size: Mapped[float | None] = mapped_column(Numeric(8, 3))

    existing_activity_households: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    weighted_existing_activity: Mapped[float] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class HouseholdDiversitySummary(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "household_diversity_summary"
    __table_args__ = (
        UniqueConstraint(
            "survey_id",
            "level",
            "province",
            "district",
            "sector",
            name="uq_household_diversity_summary_cell",
        ),
        Index("ix_household_diversity_survey_level", "survey_id", "level"),
    )

    survey_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("surveys.id", ondelete="CASCADE"), nullable=False
    )
    level: Mapped[GeoLevel] = mapped_column(value_enum(GeoLevel, "geo_level", 16), nullable=False)
    province: Mapped[str | None] = mapped_column(String(120))
    district: Mapped[str | None] = mapped_column(String(120))
    sector: Mapped[str | None] = mapped_column(String(120))

    households_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    weighted_households_total: Mapped[float] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )
    weighted_average_diversity: Mapped[float | None] = mapped_column(Numeric(8, 4))
    average_diversity: Mapped[float | None] = mapped_column(Numeric(8, 4))
    distribution: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ReadinessSummary(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "readiness_summary"
    __table_args__ = (
        UniqueConstraint(
            "survey_id",
            "level",
            "province",
            "district",
            "sector",
            name="uq_readiness_summary_cell",
        ),
        Index("ix_readiness_summary_survey_level", "survey_id", "level"),
    )

    survey_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("surveys.id", ondelete="CASCADE"), nullable=False
    )
    level: Mapped[GeoLevel] = mapped_column(value_enum(GeoLevel, "geo_level", 16), nullable=False)
    province: Mapped[str | None] = mapped_column(String(120))
    district: Mapped[str | None] = mapped_column(String(120))
    sector: Mapped[str | None] = mapped_column(String(120))

    households_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    weighted_households_total: Mapped[float] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )
    components: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    score: Mapped[float | None] = mapped_column(Numeric(6, 2))
    weights: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
