"""Product, planting, task, notification and support schemas."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import (
    AgeGroup,
    FundingStatus,
    HarvestUse,
    IrrigationFrequency,
    PlantingStatus,
    ProductStatus,
    RepeatRule,
    SpecialistRequestStatus,
    SpecialistType,
    TaskInstanceStatus,
    TaskType,
    TimeOfDay,
)


class ProductBenefitWrite(BaseModel):
    age_group: AgeGroup
    benefit_text: str = Field(min_length=1)


class ProductNutrientWrite(BaseModel):
    nutrient: str = Field(min_length=1, max_length=60)
    amount_per_100g: float | None = Field(default=None, ge=0)
    unit: str | None = Field(default=None, max_length=20)
    source: str | None = Field(default=None, max_length=2000)


class ScheduleTaskWrite(BaseModel):
    task_type: TaskType
    day_offset: int = Field(ge=0, le=3650)
    repeat_rule: RepeatRule = RepeatRule.ONCE
    repeat_weekday: int | None = Field(default=None, ge=0, le=6)
    repeat_interval_days: int | None = Field(default=None, ge=1, le=365)
    repeat_until_day: int | None = Field(default=None, ge=0, le=3650)
    time_of_day: TimeOfDay = TimeOfDay.MORNING
    instruction_text: str = Field(min_length=1)
    reminder_enabled: bool = True


class ScheduleTaskRead(ScheduleTaskWrite):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID


class RecipeWrite(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    ingredients: list[str] = []
    steps: str | None = None


class ResourceWrite(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    quantity: str | None = Field(default=None, max_length=120)
    estimated_cost: float | None = Field(default=None, ge=0)


class ProductWrite(BaseModel):
    food_item_id: uuid.UUID | None = None
    name_en: str | None = Field(default=None, max_length=160)
    name_rw: str | None = Field(default=None, max_length=160)
    photo_url: str | None = Field(default=None, max_length=500)
    description_en: str | None = None
    description_rw: str | None = None
    how_not_to_use: str | None = None
    how_to_preserve: str | None = None
    conditions_diseases: str | None = None
    reviewer_name: str | None = Field(default=None, max_length=120)
    reviewer_organisation: str | None = Field(default=None, max_length=160)
    review_date: date | None = None
    time_to_harvest_min_days: int | None = Field(default=None, ge=0, le=3650)
    time_to_harvest_max_days: int | None = Field(default=None, ge=0, le=3650)
    harvest_method: str | None = None
    irrigation_method: str | None = None
    irrigation_frequency: IrrigationFrequency | None = None
    irrigation_time_of_day: TimeOfDay | None = None
    place_type: str | None = Field(default=None, max_length=60)
    temperature_min_c: float | None = None
    temperature_max_c: float | None = None
    humidity_min_pct: float | None = Field(default=None, ge=0, le=100)
    humidity_max_pct: float | None = Field(default=None, ge=0, le=100)
    resources: list[ResourceWrite] = []
    recipes: list[RecipeWrite] = []
    season_suitability: dict[str, Any] = {}
    spray_details: dict[str, Any] = {}
    needs_expert_review: bool | None = None
    growing_steps_validated: bool | None = None
    benefits: list[ProductBenefitWrite] | None = None
    nutrients: list[ProductNutrientWrite] | None = None
    schedule_tasks: list[ScheduleTaskWrite] | None = None

    @field_validator("time_to_harvest_max_days")
    @classmethod
    def _max_after_min(cls, value: int | None, info) -> int | None:  # noqa: ANN001
        minimum = info.data.get("time_to_harvest_min_days")
        if value is not None and minimum is not None and value < minimum:
            raise ValueError("time_to_harvest_max_days must be at least the minimum")
        return value


class ProductBenefitRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    age_group: AgeGroup
    benefit_text: str | None = None


class ProductNutrientRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    nutrient: str
    amount_per_100g: float | None = None
    unit: str | None = None
    source: str | None = None


class ProductRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    food_item_id: uuid.UUID | None = None
    name_en: str
    name_rw: str | None = None
    photo_url: str | None = None
    description_en: str | None = None
    description_rw: str | None = None
    status: ProductStatus
    version: int
    time_to_harvest_min_days: int | None = None
    time_to_harvest_max_days: int | None = None
    reviewer_name: str | None = None
    reviewer_organisation: str | None = None
    review_date: date | None = None
    needs_expert_review: bool
    growing_steps_validated: bool
    published_at: datetime | None = None
    created_at: datetime


class ProductDetail(ProductRead):
    how_not_to_use: str | None = None
    how_to_preserve: str | None = None
    conditions_diseases: str | None = None
    conditions_note_translation_key: str = "products.health.note"
    harvest_method: str | None = None
    irrigation_method: str | None = None
    irrigation_frequency: IrrigationFrequency | None = None
    irrigation_time_of_day: TimeOfDay | None = None
    place_type: str | None = None
    temperature_min_c: float | None = None
    temperature_max_c: float | None = None
    humidity_min_pct: float | None = None
    humidity_max_pct: float | None = None
    resources: list[dict[str, Any]] = []
    recipes: list[dict[str, Any]] = []
    season_suitability: dict[str, Any] = {}
    spray_details: dict[str, Any] = {}
    benefits: list[ProductBenefitRead] = []
    nutrients: list[ProductNutrientRead] = []
    schedule_tasks: list[ScheduleTaskRead] = []


class TrackableBlocker(BaseModel):
    field: str
    translation_key: str


class ProductStatusRow(BaseModel):
    id: uuid.UUID
    name_en: str
    status: ProductStatus
    version: int
    reviewer_name: str | None = None
    review_date: date | None = None
    farmers_planting: int = 0
    last_edited_at: datetime | None = None
    blockers: list[TrackableBlocker] = []


class TrackableResponse(BaseModel):
    product: ProductRead
    blockers: list[TrackableBlocker] = []


class ProvinceContext(BaseModel):
    """The farmer facing province card, hidden when the cell is suppressed."""

    available: bool
    province: str | None = None
    share: float | None = None
    suppressed: bool = False
    translation_key: str | None = None
    source_name: str | None = None
    source_year: int | None = None
    survey_name: str | None = None


class FarmerProductDetail(ProductDetail):
    province_context: ProvinceContext | None = None


class ChecklistItem(BaseModel):
    name: str
    quantity: str | None = None
    estimated_cost: float | None = None
    owned: bool = False


class PlantingCreate(BaseModel):
    product_id: uuid.UUID
    quantity: int = Field(default=1, ge=1, le=1000)
    started_on: date | None = None
    checklist: list[ChecklistItem] = []


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    planting_id: uuid.UUID
    task_type: TaskType
    due_at: datetime
    time_of_day: TimeOfDay
    instruction_text: str
    status: TaskInstanceStatus
    completed_at: datetime | None = None
    snoozed_until: datetime | None = None
    skipped_reason: str | None = None
    is_urgent: bool


class PlantingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    product_version: int
    status: PlantingStatus
    quantity: int
    started_on: date
    expected_harvest_on: date | None = None
    notes: str | None = None
    created_at: datetime


class PlantingSummary(PlantingRead):
    product_name_en: str = ""
    product_name_rw: str | None = None
    photo_url: str | None = None
    progress_percent: int = 0
    days_left: int | None = None
    next_task: TaskRead | None = None
    checklist_snapshot: list[dict[str, Any]] = []


class PlantingDetail(PlantingSummary):
    tasks: list[TaskRead] = []
    harvests: list[dict[str, Any]] = []


class PlantingCreateResponse(BaseModel):
    planting: PlantingSummary
    first_tasks: list[TaskRead]
    expected_harvest_on: date | None = None


class PlantingUpdate(BaseModel):
    status: PlantingStatus | None = None
    notes: str | None = Field(default=None, max_length=2000)


class SnoozeRequest(BaseModel):
    hours: int = Field(default=4, ge=1, le=72)


class SkipRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class TodayResponse(BaseModel):
    date: date
    morning: list[TaskRead]
    evening: list[TaskRead]
    completed_today: int = 0


class NotificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: str
    title_translation_key: str
    body_translation_key: str
    params: dict[str, Any] = {}
    task_instance_id: uuid.UUID | None = None
    sms_sent: bool
    read_at: datetime | None = None
    created_at: datetime


class HarvestCreate(BaseModel):
    planting_id: uuid.UUID
    harvested_on: date | None = None
    quantity_kg: float | None = Field(default=None, ge=0, le=1_000_000)
    quantity_note: str | None = Field(default=None, max_length=200)
    used_for: HarvestUse
    notes: str | None = Field(default=None, max_length=2000)


class HarvestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    planting_id: uuid.UUID
    harvested_on: date
    quantity_kg: float | None = None
    quantity_note: str | None = None
    used_for: HarvestUse
    notes: str | None = None
    created_at: datetime


class HarvestSummary(BaseModel):
    total_kg: float = 0
    by_use: dict[str, float] = {}
    count: int = 0


class SpecialistRequestCreate(BaseModel):
    product_id: uuid.UUID | None = None
    specialist_type: SpecialistType
    description: str = Field(min_length=5, max_length=2000)
    photo_url: str | None = Field(default=None, max_length=500)


class SpecialistRequestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID | None = None
    specialist_type: SpecialistType
    description: str
    photo_url: str | None = None
    status: SpecialistRequestStatus
    assigned_to: str | None = None
    contacted_at: datetime | None = None
    closed_at: datetime | None = None
    created_at: datetime


class SpecialistRequestUpdate(BaseModel):
    status: SpecialistRequestStatus | None = None
    assigned_to: str | None = Field(default=None, max_length=160)
    admin_notes: str | None = Field(default=None, max_length=4000)


class FundingInterestCreate(BaseModel):
    product_id: uuid.UUID | None = None
    planned_size: str = Field(min_length=1, max_length=200)
    location: str = Field(min_length=1, max_length=200)
    building_or_land: str = Field(min_length=1, max_length=200)
    experience: str | None = Field(default=None, max_length=2000)
    funding_needed: float | None = Field(default=None, ge=0)
    consent_to_share: bool

    @field_validator("consent_to_share")
    @classmethod
    def _consent_required(cls, value: bool) -> bool:
        if not value:
            raise ValueError("consent to share with BRD is required")
        return value


class FundingInterestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID | None = None
    planned_size: str
    location: str
    building_or_land: str
    experience: str | None = None
    funding_needed: float | None = None
    consent_to_share: bool
    consent_at: datetime | None = None
    status: FundingStatus
    referred_to_brd_at: datetime | None = None
    brd_reference: str | None = None
    created_at: datetime


class FundingInterestUpdate(BaseModel):
    status: FundingStatus | None = None
    admin_notes: str | None = Field(default=None, max_length=4000)


class ReferToBrdRequest(BaseModel):
    brd_reference: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=2000)


class MessageTemplateWrite(BaseModel):
    body: str = Field(min_length=1, max_length=2000)
    sms_body: str | None = Field(default=None, max_length=640)
    variables: list[str] = []


class MessageTemplateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    key: str
    language: str
    body: str
    sms_body: str | None = None
    variables: list[str] = []


class ReminderRunRead(BaseModel):
    reminders_created: int
    follow_ups_created: int
    tasks_marked_missed: int
    sms_sent: int
    sms_failed: int
    suppressed_quiet_hours: int
    suppressed_limit: int
    skipped: int
    errors: list[str] = []


class ReminderPreferences(BaseModel):
    quiet_hours_start: time | None = None
    quiet_hours_end: time | None = None
    reminder_morning_time: time | None = None
    reminder_evening_time: time | None = None
    message_opt_in: bool | None = None


SortOrder = Literal["asc", "desc"]
