"""Nutrition reference, ranking runs and their items."""

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
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin, value_enum
from app.models.enums import RankingStatus


class NutritionReference(Base, UUIDPrimaryKeyMixin):
    """Nutrient values per 100 g. Seeded empty: only experts fill this."""

    __tablename__ = "nutrition_reference"
    __table_args__ = (
        UniqueConstraint(
            "food_item_id",
            "nutrient",
            name="uq_nutrition_reference_food_nutrient",
        ),
        Index("ix_nutrition_reference_nutrient", "nutrient"),
    )

    food_item_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("food_items.id", ondelete="CASCADE"), nullable=False
    )
    nutrient: Mapped[str] = mapped_column(String(60), nullable=False)
    amount_per_100g: Mapped[float | None] = mapped_column(Numeric(14, 4))
    unit: Mapped[str | None] = mapped_column(String(20))
    source: Mapped[str | None] = mapped_column(Text)
    reviewer_name: Mapped[str | None] = mapped_column(String(120))
    review_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class AiRankingRun(Base, UUIDPrimaryKeyMixin):
    """One reproducible ranking run: same weights and input give the same output."""

    __tablename__ = "ai_ranking_runs"
    __table_args__ = (Index("ix_ai_ranking_runs_survey_created", "survey_id", "created_at"),)

    survey_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("surveys.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[RankingStatus] = mapped_column(
        value_enum(RankingStatus, "ranking_status", 16),
        nullable=False,
        default=RankingStatus.PENDING,
        server_default=RankingStatus.PENDING.value,
    )
    weights: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    score_basis: Mapped[str] = mapped_column(
        String(30), nullable=False, default="gap_only", server_default="gap_only"
    )
    nutrition_needs: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    cluster_count: Mapped[int | None] = mapped_column(Integer)
    silhouette: Mapped[float | None] = mapped_column(Numeric(8, 4))
    fingerprint: Mapped[str | None] = mapped_column(String(64))
    warnings: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("admins.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AiRankingItem(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "ai_ranking_items"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "food_item_id", "province", name="uq_ai_ranking_items_run_food_province"
        ),
        Index("ix_ai_ranking_items_run_rank", "run_id", "rank"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("ai_ranking_runs.id", ondelete="CASCADE"), nullable=False
    )
    food_item_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("food_items.id", ondelete="CASCADE"), nullable=False
    )
    province: Mapped[str | None] = mapped_column(String(120))
    rank: Mapped[int] = mapped_column(Integer, nullable=False)

    gap_score: Mapped[float | None] = mapped_column(Numeric(8, 4))
    nutrition_value: Mapped[float | None] = mapped_column(Numeric(8, 4))
    nutrition_need: Mapped[float | None] = mapped_column(Numeric(8, 4))
    feasibility: Mapped[float | None] = mapped_column(Numeric(8, 4))
    total_score: Mapped[float | None] = mapped_column(Numeric(8, 4))

    cluster_label: Mapped[str | None] = mapped_column(String(60))
    reason_fields: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    pinned: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )


class ProvinceCluster(Base, UUIDPrimaryKeyMixin):
    """KMeans labels for the geography units of one ranking run."""

    __tablename__ = "province_clusters"
    __table_args__ = (
        UniqueConstraint("run_id", "level", "unit", name="uq_province_clusters_run_unit"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("ai_ranking_runs.id", ondelete="CASCADE"), nullable=False
    )
    level: Mapped[str] = mapped_column(String(20), nullable=False)
    unit: Mapped[str] = mapped_column(String(120), nullable=False)
    cluster_label: Mapped[str] = mapped_column(String(60), nullable=False)
    summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
