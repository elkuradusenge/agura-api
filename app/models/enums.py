"""Enumerations shared by models and schemas."""

from __future__ import annotations

from enum import StrEnum


class Language(StrEnum):
    RW = "rw"
    EN = "en"
    FR = "fr"


class UserStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class AdminRole(StrEnum):
    SUPER_ADMIN = "super_admin"
    CONTENT_EDITOR = "content_editor"
    DATA_ANALYST = "data_analyst"
    SUPPORT_AGENT = "support_agent"


class AdminStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class SubjectType(StrEnum):
    USER = "user"
    ADMIN = "admin"


class ActorType(StrEnum):
    USER = "user"
    ADMIN = "admin"
    SYSTEM = "system"


class SurveyStatus(StrEnum):
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    AWAITING_MAPPING = "awaiting_mapping"
    READY = "ready"
    COMPLETED = "completed"
    FAILED = "failed"
    DELETED = "deleted"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_MAPPING = "awaiting_mapping"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobStage(StrEnum):
    RECEIVED = "received"
    VALIDATED = "validated"
    MAPPED = "mapped"
    CLEANED = "cleaned"
    LOADED = "loaded"
    AGGREGATED = "aggregated"
    SCORED = "scored"
    COMPLETED = "completed"


STAGE_ORDER: tuple[JobStage, ...] = (
    JobStage.RECEIVED,
    JobStage.VALIDATED,
    JobStage.MAPPED,
    JobStage.CLEANED,
    JobStage.LOADED,
    JobStage.AGGREGATED,
    JobStage.SCORED,
    JobStage.COMPLETED,
)


class PlatformGroup(StrEnum):
    GEOGRAPHY = "geography"
    PERSON_DEMOGRAPHICS = "person_demographics"
    EDUCATION_DIGITAL = "education_digital"
    EMPLOYMENT = "employment"
    HOUSING_SERVICES = "housing_services"
    ASSETS = "assets"
    LIVESTOCK = "livestock"
    FIELD_CROPS = "field_crops"
    VEGETABLES = "vegetables"
    FRUITS = "fruits"
    TEA_COFFEE = "tea_coffee"
    WEIGHTS = "weights"
    PERSON_IDENTIFIERS = "person_identifiers"
    UNKNOWN = "unknown"


class FoodGroup(StrEnum):
    FIELD_CROP = "field_crop"
    VEGETABLE = "vegetable"
    FRUIT = "fruit"
    LIVESTOCK = "livestock"
    FUNGI = "fungi"


class RankingStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class FeasibilityLevel(StrEnum):
    UNKNOWN = "unknown"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class WsChannel(StrEnum):
    ADMIN = "admin"
    USER = "user"


class GeoLevel(StrEnum):
    NATIONAL = "national"
    PROVINCE = "province"
    DISTRICT = "district"
    SECTOR = "sector"


GEO_LEVELS: tuple[GeoLevel, ...] = (
    GeoLevel.NATIONAL,
    GeoLevel.PROVINCE,
    GeoLevel.DISTRICT,
    GeoLevel.SECTOR,
)


class JobEventType(StrEnum):
    UPLOAD_RECEIVED = "upload.received"
    JOB_STARTED = "job.started"
    JOB_STAGE_STARTED = "job.stage_started"
    JOB_STAGE_PROGRESS = "job.stage_progress"
    JOB_MAPPING_REQUIRED = "job.mapping_required"
    JOB_STAGE_COMPLETED = "job.stage_completed"
    JOB_COMPLETED = "job.completed"
    JOB_FAILED = "job.failed"
    RANKING_COMPLETED = "ranking.completed"
    NOTIFICATION_CREATED = "notification.created"
    TASK_DUE = "task.due"
    PRODUCT_PUBLISHED = "product.published"
    PRODUCT_UPDATED = "product.updated"
    ANALYSIS_UPDATED = "analysis.updated"
    SPECIALIST_REQUEST_UPDATED = "specialist_request.updated"
    FUNDING_INTEREST_UPDATED = "funding_interest.updated"
