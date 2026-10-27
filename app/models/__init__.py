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
    FeasibilityLevel,
    FoodGroup,
    GeoLevel,
    JobEventType,
    JobStage,
    JobStatus,
    Language,
    PlatformGroup,
    RankingStatus,
    SubjectType,
    SurveyStatus,
    UserStatus,
    WsChannel,
)
from app.models.household import FoodItem, Household
from app.models.password_reset_code import PasswordResetCode
from app.models.person import Person
from app.models.ranking import (
    AiRankingItem,
    AiRankingRun,
    NutritionReference,
    ProvinceCluster,
)
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
from app.models.ws_event import WsEvent, WsEventCursor

__all__ = [
    "ActorType",
    "Admin",
    "AiRankingItem",
    "AiRankingRun",
    "AdminRole",
    "AdminStatus",
    "AppSetting",
    "AuditLog",
    "Base",
    "FeasibilityLevel",
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
    "NutritionReference",
    "PasswordResetCode",
    "Person",
    "PlatformGroup",
    "ProvinceCluster",
    "ProvinceFoodSummary",
    "RankingStatus",
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
    "WsChannel",
    "WsEvent",
    "WsEventCursor",
]
