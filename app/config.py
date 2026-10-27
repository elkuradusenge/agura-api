"""Application configuration loaded from environment variables and Secrets.

Nothing that decides *where* the application points has a default. If
`DATABASE_URL`, `JWT_SECRET` or `ALLOWED_ORIGINS` is missing or malformed the
process refuses to start, the same way `envalid` does it in Node: every problem
is collected and reported in one pass, instead of failing on the first one, and
the report never echoes a secret value.
"""

from __future__ import annotations

import sys
from functools import lru_cache
from typing import Any, Literal

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Values shipped in templates or copied from examples. They must never be
# accepted as a real signing key, because a known key means forgeable tokens.
PLACEHOLDER_SECRETS = frozenset(
    {
        "",
        "change-me",
        "change-me-to-a-long-random-string",
        "development-only-secret-change-me",
        "secret",
        "changeme",
    }
)

POSTGRES_SCHEMES = ("postgres://", "postgresql://", "postgresql+psycopg://")


def _normalise_database_url(url: str) -> str:
    """Force the psycopg (v3) driver so SQLAlchemy 2 talks to PostgreSQL the same way everywhere."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


class InvalidEnvironment(RuntimeError):
    """Raised when required environment variables are missing or malformed.

    The message is a ready to print report: one line per problem.
    """


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    app_name: str = "AGUra API"
    api_prefix: str = "/api/v1"
    environment: Literal["development", "test", "production", "prod"] = "development"

    # Required. A default here once pointed the app at a database that did not
    # exist, which surfaced much later as a confusing connection error.
    database_url: str = Field(min_length=1)
    allowed_origins: str = Field(min_length=1)
    jwt_secret: str = Field(min_length=32)

    test_database_url: str | None = None
    db_pool_size: int = 5
    db_max_overflow: int = 5
    db_echo: bool = False

    jwt_issuer: str = "agura"
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = Field(default=30, ge=1)
    refresh_token_days: int = Field(default=30, ge=1)

    admin_bootstrap_email: str = "admin@agura.rw"
    admin_bootstrap_password: str | None = None
    admin_bootstrap_name: str = "AGUra Administrator"

    sms_enabled: bool = False
    sms_provider: Literal["console", "disabled", "africastalking"] = "console"
    sms_api_key: str | None = None
    sms_username: str | None = None
    sms_sender_id: str = "AGUra"

    port: int = Field(default=8000, ge=1, le=65535)
    log_level: Literal["debug", "info", "warning", "error", "critical"] = "info"

    small_cell_threshold: int = Field(default=10, ge=1)

    phone_verification_enabled: bool = False

    auth_rate_limit_attempts: int = Field(default=10, ge=1)
    auth_rate_limit_window_seconds: int = Field(default=60, ge=1)

    password_min_length: int = Field(default=8, ge=8)
    password_reset_code_minutes: int = Field(default=15, ge=1)

    upload_dir: str = "uploads"
    max_upload_bytes: int = Field(default=2 * 1024 * 1024 * 1024, ge=1)
    min_free_disk_bytes: int = Field(default=2 * 1024 * 1024 * 1024, ge=0)
    keep_uploaded_files: bool = False

    analysis_chunk_size: int = Field(default=25_000, ge=1)
    load_batch_size: int = Field(default=5_000, ge=1)
    job_workers: int = Field(default=1, ge=1)
    run_jobs_inline: bool = False
    worker_poll_seconds: float = Field(default=2.0, gt=0)
    ws_auth_timeout_seconds: float = Field(default=15.0, gt=0)

    survey_key_variables: str = "ml01,ml02,hhid,hh_weight"
    dta_encoding: str | None = None
    resident_retention_days: int = Field(default=365, ge=1)

    timezone: str = "Africa/Kigali"

    @field_validator("database_url", "test_database_url")
    @classmethod
    def _validate_database_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalised = _normalise_database_url(value.strip())
        if not normalised.startswith(POSTGRES_SCHEMES):
            raise ValueError(
                "must be a PostgreSQL URL, for example "
                "postgresql://user:password@127.0.0.1:5432/dbname"
            )
        return normalised

    @field_validator("jwt_secret")
    @classmethod
    def _reject_placeholder_secret(cls, value: str) -> str:
        if value.strip().lower() in PLACEHOLDER_SECRETS:
            raise ValueError(
                "is still the example placeholder, set a real random value, "
                'for example: python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
        return value

    @field_validator("allowed_origins")
    @classmethod
    def _reject_empty_origin(cls, value: str) -> str:
        if not [part for part in value.split(",") if part.strip()]:
            raise ValueError("must list at least one origin, for example http://localhost:5173")
        return value

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


def _env_name(location: tuple[Any, ...]) -> str:
    return ".".join(str(part) for part in location if part != "__root__").upper() or "ENVIRONMENT"


def _reason(error: dict[str, Any]) -> str:
    """A short, human reason. Never the offending value, which may be a secret."""
    kind = error.get("type", "")
    context = error.get("ctx") or {}

    if kind == "missing":
        return "required, but it is not set"
    if kind == "literal_error":
        return f"must be one of: {context.get('expected', '')}"
    if kind == "string_too_short":
        return f"must be at least {context.get('min_length')} characters"
    if kind == "string_too_long":
        return f"must be at most {context.get('max_length')} characters"
    if kind in {"int_parsing", "int_from_float"}:
        return "must be a whole number"
    if kind == "float_parsing":
        return "must be a number"
    if kind == "bool_parsing":
        return "must be true or false"
    if kind == "greater_than":
        return f"must be greater than {context.get('gt')}"
    if kind == "greater_than_equal":
        return f"must be at least {context.get('ge')}"
    if kind == "less_than":
        return f"must be less than {context.get('lt')}"
    if kind == "less_than_equal":
        return f"must be at most {context.get('le')}"
    if kind == "value_error":
        return str(context.get("error") or error.get("msg", "is invalid"))
    return str(error.get("msg", "is invalid"))


def format_environment_report(errors: list[dict[str, Any]]) -> str:
    """One aligned line per problem, in the spirit of envalid's aggregated report."""
    rows = sorted({(_env_name(tuple(error["loc"])), _reason(error)) for error in errors})
    width = max(len(name) for name, _ in rows)

    lines = ["", "Environment validation failed", "=" * 30]
    lines.extend(f"  {name.ljust(width)}  {reason}" for name, reason in rows)
    lines.extend(
        [
            "=" * 30,
            "Set these in .env or in the process environment, then start again.",
            "",
        ]
    )
    return "\n".join(lines)


@lru_cache
def get_settings() -> Settings:
    """Load and validate once. Raises InvalidEnvironment listing every problem."""
    try:
        return Settings()
    except ValidationError as exc:
        raise InvalidEnvironment(format_environment_report(exc.errors())) from None


def clear_settings_cache() -> None:
    """Drop the cached settings, so a test can change the environment."""
    get_settings.cache_clear()


def main() -> int:
    """`python -m app.config`: check the environment without starting the API."""
    try:
        settings = get_settings()
    except InvalidEnvironment as exc:
        print(exc, file=sys.stderr)
        return 1

    print(f"Environment is valid ({settings.environment}).")
    print(f"  DATABASE_URL  {settings.database_url.split('@')[-1]}")
    print(f"  JWT_SECRET    set, {len(settings.jwt_secret)} characters")
    print(f"  ORIGINS       {', '.join(settings.cors_origins)}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
