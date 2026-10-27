"""Analysis response schemas.

Aggregate values are wrapped so a suppressed cell is explicit rather than a zero:
clients render the translation key for "fewer than 10" instead of a number.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import FoodGroup, GeoLevel


class SuppressedValue(BaseModel):
    value: float | int | None = None
    count: int | None = None
    suppressed: bool = False
    translation_key: str | None = None
    threshold: int


class ShareValue(SuppressedValue):
    """A share is suppressed on the same household count as the numerator."""


class FoodItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    standard_name: str
    local_name: str | None = None
    group: FoodGroup
    source_variable: str
    count_variable: str | None = None
    nutrition_group: str | None = None


class FoodItemUpdate(BaseModel):
    standard_name: str | None = Field(default=None, min_length=2, max_length=120)
    local_name: str | None = Field(default=None, max_length=120)
    group: FoodGroup | None = None
    nutrition_group: str | None = Field(default=None, max_length=60)


class AnalysisMeta(BaseModel):
    survey_id: uuid.UUID
    survey_name: str
    survey_year: int
    source_name: str
    source_year: int
    small_cell_threshold: int
    weighted: bool


class FoodSummaryRow(BaseModel):
    food_item_id: uuid.UUID
    standard_name: str
    group: FoodGroup
    level: GeoLevel
    province: str | None = None
    district: str | None = None
    sector: str | None = None
    households_total: SuppressedValue
    households_with_food: SuppressedValue
    share: ShareValue
    unweighted_share: ShareValue
    average_count: SuppressedValue | None = None
    rearing_district_distribution: dict[str, int] | None = None


class FoodSummaryResponse(BaseModel):
    meta: AnalysisMeta
    items: list[FoodSummaryRow]
    pagination: dict[str, int]


class MushroomCell(BaseModel):
    level: GeoLevel
    province: str | None = None
    district: str | None = None
    sector: str | None = None
    households_total: SuppressedValue
    mushroom_households: SuppressedValue
    share: ShareValue
    unweighted_share: ShareValue
    urban_mushroom_households: SuppressedValue
    rural_mushroom_households: SuppressedValue
    urban_share_of_mushroom: ShareValue
    rural_share_of_mushroom: ShareValue
    female_headed_mushroom_households: SuppressedValue
    female_headed_share: ShareValue
    average_household_size: SuppressedValue
    mushroom_average_household_size: SuppressedValue
    existing_activity_households: SuppressedValue
    existing_activity_share: ShareValue


class MushroomResponse(BaseModel):
    meta: AnalysisMeta
    items: list[MushroomCell]
    pagination: dict[str, int]


class DiversityCell(BaseModel):
    level: GeoLevel
    province: str | None = None
    district: str | None = None
    sector: str | None = None
    households_total: SuppressedValue
    average_diversity: SuppressedValue
    weighted_average_diversity: SuppressedValue
    distribution: dict[str, int] | None = None


class DiversityResponse(BaseModel):
    meta: AnalysisMeta
    items: list[DiversityCell]
    pagination: dict[str, int]


class ReadinessComponent(BaseModel):
    households: int | None = None
    weighted_share: float | None = None
    weight: float | None = None
    definition: Any = None
    definition_source: str | None = None
    suppressed: bool = False


class ReadinessCell(BaseModel):
    level: GeoLevel
    province: str | None = None
    district: str | None = None
    sector: str | None = None
    households_total: SuppressedValue
    score: SuppressedValue
    components: dict[str, ReadinessComponent]
    weights: dict[str, float]


class ReadinessResponse(BaseModel):
    meta: AnalysisMeta
    items: list[ReadinessCell]
    pagination: dict[str, int]


class AnalysisOverview(BaseModel):
    survey_id: uuid.UUID
    updated_at: datetime | None = None
    national_households: int = 0
    food_items_aggregated: int = 0
    mushroom_households: int | None = None
    top_under_farmed: list[dict[str, Any]] = []
    readiness_score: float | None = None
