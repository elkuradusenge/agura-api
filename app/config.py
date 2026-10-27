"""Application configuration loaded from environment variables and Secrets."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _normalise_database_url(url: str) -> str:
    """Force the psycopg (v3) driver so SQLAlchemy 2 talks to PostgreSQL the same way everywhere."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    app_name: str = "AGUra API"
    api_prefix: str = "/api/v1"
    environment: str = "development"

    database_url: str = "postgresql+psycopg://agura:agura@127.0.0.1:5432/agura"
    test_database_url: str | None = None
    db_pool_size: int = 5
    db_max_overflow: int = 5
    db_echo: bool = False

    jwt_secret: str = "development-only-secret-change-me"
    jwt_issuer: str = "agura"
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 30
    refresh_token_days: int = 30

    admin_bootstrap_email: str = "admin@agura.rw"
    admin_bootstrap_password: str | None = None
    admin_bootstrap_name: str = "AGUra Administrator"

    allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    sms_enabled: bool = False
    sms_provider: str = "console"
    sms_api_key: str | None = None
    sms_username: str | None = None
    sms_sender_id: str = "AGUra"

    port: int = 8000
    log_level: str = "info"

    small_cell_threshold: int = Field(default=10, ge=1)

    phone_verification_enabled: bool = False

    auth_rate_limit_attempts: int = Field(default=10, ge=1)
    auth_rate_limit_window_seconds: int = Field(default=60, ge=1)

    password_min_length: int = 8
    password_reset_code_minutes: int = 15

    upload_dir: str = "uploads"
    max_upload_bytes: int = 2 * 1024 * 1024 * 1024
    min_free_disk_bytes: int = 2 * 1024 * 1024 * 1024
    keep_uploaded_files: bool = False

    analysis_chunk_size: int = 25_000
    load_batch_size: int = 5_000
    job_workers: int = 1
    run_jobs_inline: bool = False
    worker_poll_seconds: float = 2.0
    ws_auth_timeout_seconds: float = 15.0

    survey_key_variables: str = "ml01,ml02,hhid,hh_weight"
    dta_encoding: str | None = None
    resident_retention_days: int = 365

    timezone: str = "Africa/Kigali"

    @field_validator("database_url", "test_database_url")
    @classmethod
    def _validate_database_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _normalise_database_url(value)

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}

    @property
    def is_test(self) -> bool:
        return self.environment.lower() == "test"

    @property
    def required_survey_variables(self) -> list[str]:
        return [name.strip() for name in self.survey_key_variables.split(",") if name.strip()]

    def sqlalchemy_url(self, *, use_test_database: bool = False) -> str:
        if use_test_database and self.test_database_url:
            return self.test_database_url
        return self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
