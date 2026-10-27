"""AI suggestion and nutrition reference schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import FeasibilityLevel, RankingStatus


class NutritionRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    food_item_id: uuid.UUID
    standard_name: str | None = None
    nutrient: str
    amount_per_100g: float | None = None
    unit: str | None = None
    source: str | None = None
    reviewer_name: str | None = None
    review_date: datetime | None = None


class NutritionImportResult(BaseModel):
    imported: int
    skipped: int
    errors: list[str] = []


class NutritionUpsert(BaseModel):
    food_item_id: uuid.UUID
    nutrient: str = Field(min_length=1, max_length=60)
    amount_per_100g: float | None = Field(default=None, ge=0)
    unit: str | None = Field(default=None, max_length=20)
    source: str | None = Field(default=None, max_length=2000)
    reviewer_name: str | None = Field(default=None, max_length=120)
    review_date: datetime | None = None


class RankingWeights(BaseModel):
    nutrition_value: float | None = Field(default=None, ge=0, le=1)
    nutrition_need: float | None = Field(default=None, ge=0, le=1)
    feasibility: float | None = Field(default=None, ge=0, le=1)
    gap: float | None = Field(default=None, ge=0, le=1)


class RankingRequest(BaseModel):
    survey_id: uuid.UUID | None = None
    weights: RankingWeights | None = None
    nutrition_needs: dict[str, float] | None = None
    provinces: list[str] | None = None


class RankingItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    rank: int
    food_item_id: uuid.UUID
    standard_name: str | None = None
    province: str | None = None
    gap_score: float | None = None
    nutrition_value: float | None = None
    nutrition_need: float | None = None
    feasibility: float | None = None
    total_score: float | None = None
    cluster_label: str | None = None
    explanation: str
    pinned: bool
    reason_fields: dict[str, Any] = {}


class ClusterRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    level: str
    unit: str
    cluster_label: str
    summary: dict[str, Any] = {}


class RankingRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    survey_id: uuid.UUID | None = None
    status: RankingStatus
    weights: dict[str, Any]
    nutrition_needs: dict[str, Any] = {}
    score_basis: str
    cluster_count: int | None = None
    silhouette: float | None = None
    fingerprint: str | None = None
    warnings: list[str] = []
    created_at: datetime
    completed_at: datetime | None = None


class RankingRunDetail(RankingRunRead):
    items: list[RankingItemRead] = []
    clusters: list[ClusterRead] = []


class FoodItemFeasibility(BaseModel):
    budget_fit: FeasibilityLevel | None = None
    land_need: FeasibilityLevel | None = None
    season_fit: FeasibilityLevel | None = None
    market_note: str | None = Field(default=None, max_length=500)
