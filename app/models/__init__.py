"""Import every model so Base.metadata is complete for Alembic and tests."""

from app.models.admin import Admin
from app.models.analysis import (
    HouseholdDiversitySummary,
    MushroomSummary,
    ProvinceFoodSummary,
    ReadinessSummary,
)
from app.models.app_settings import AppSetting
from app.models.audit_log import AuditLog
from app.models.base import Base
from app.models.enums import (
    ActorType,
    AdminRole,
    AdminStatus,
    FoodGroup,
    GeoLevel,
    JobEventType,
    JobStage,
    JobStatus,
    Language,
    PlatformGroup,
    SubjectType,
    SurveyStatus,
    UserStatus,
)
from app.models.household import FoodItem, Household
from app.models.password_reset_code import PasswordResetCode
from app.models.person import Person
from app.models.refresh_token import RefreshToken
from app.models.survey import (
    FieldMapping,
    JobEvent,
    Survey,
    SurveyJob,
    UploadQualityReport,
    ValueLabel,
    VariableDictionary,
)
from app.models.user import User

__all__ = [
    "ActorType",
    "Admin",
    "AdminRole",
    "AdminStatus",
    "AppSetting",
    "AuditLog",
    "Base",
    "FieldMapping",
    "FoodGroup",
    "FoodItem",
    "GeoLevel",
    "Household",
    "HouseholdDiversitySummary",
    "JobEvent",
    "JobEventType",
    "JobStage",
    "JobStatus",
    "Language",
    "MushroomSummary",
    "PasswordResetCode",
    "Person",
    "PlatformGroup",
    "ProvinceFoodSummary",
    "ReadinessSummary",
    "RefreshToken",
    "SubjectType",
    "Survey",
    "SurveyJob",
    "SurveyStatus",
    "UploadQualityReport",
    "User",
    "UserStatus",
    "ValueLabel",
    "VariableDictionary",
]
