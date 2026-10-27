"""Shared pytest fixtures. The suite runs against a real PostgreSQL database."""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://postgres@127.0.0.1:5433/agura_test")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("JWT_SECRET", "test-secret-used-only-by-the-test-suite")
os.environ.setdefault("ADMIN_BOOTSTRAP_PASSWORD", "BootstrapPass123!")
os.environ.setdefault("SMS_ENABLED", "false")
os.environ.setdefault("RUN_JOBS_INLINE", "true")
os.environ.setdefault("ANALYSIS_CHUNK_SIZE", "500")
os.environ.setdefault("UPLOAD_DIR", ".test-uploads")

import uuid  # noqa: E402
from collections.abc import Iterator  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.core.rate_limit import reset_rate_limits  # noqa: E402
from app.core.security import create_access_token, hash_password  # noqa: E402
from app.db import get_session_factory  # noqa: E402
from app.main import app  # noqa: E402
from app.models.admin import Admin  # noqa: E402
from app.models.enums import AdminRole, AdminStatus, SubjectType  # noqa: E402
from app.models.user import User  # noqa: E402

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TABLES = (
    "job_events",
    "survey_jobs",
    "upload_quality_report",
    "persons",
    "households",
    "field_mappings",
    "value_labels",
    "variable_dictionary",
    "surveys",
    "audit_log",
    "refresh_tokens",
    "password_reset_codes",
    "users",
    "admins",
    "food_items",
    "app_settings",
)


def _run_migrations() -> None:
    config = Config(os.path.join(PROJECT_ROOT, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(PROJECT_ROOT, "alembic"))
    command.upgrade(config, "head")


@pytest.fixture(scope="session", autouse=True)
def database() -> Iterator[None]:
    settings = get_settings()
    admin_engine = create_engine(settings.database_url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    admin_engine.dispose()
    _run_migrations()
    yield


@pytest.fixture(scope="session")
def client(database: None) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def clean_database(database: None) -> Iterator[None]:
    reset_rate_limits()
    yield
    session = get_session_factory()()
    try:
        session.execute(text(f"TRUNCATE TABLE {', '.join(TABLES)} CASCADE"))
        session.commit()
    finally:
        session.close()
    reset_rate_limits()


@pytest.fixture
def db() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def make_admin(
    db: Session,
    *,
    role: AdminRole = AdminRole.SUPER_ADMIN,
    password: str = "AdminPass123!",
    status: AdminStatus = AdminStatus.ACTIVE,
    email: str | None = None,
    phone: str | None = None,
) -> Admin:
    admin = Admin(
        name=f"Test {role.value}",
        email=email or f"{role.value}-{uuid.uuid4().hex[:8]}@agura.rw",
        phone=phone,
        password_hash=hash_password(password),
        role=role,
        status=status,
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    return admin


def make_user(
    db: Session, *, phone: str = "+250788111222", password: str = "FarmerPass123!"
) -> User:
    user = User(phone=phone, password_hash=hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def admin_factory(db: Session):
    def _factory(**kwargs) -> Admin:
        return make_admin(db, **kwargs)

    return _factory


@pytest.fixture
def user_factory(db: Session):
    def _factory(**kwargs) -> User:
        return make_user(db, **kwargs)

    return _factory


def admin_headers(admin: Admin) -> dict[str, str]:
    token, _ = create_access_token(admin.id, SubjectType.ADMIN, admin.role.value)
    return {"Authorization": f"Bearer {token}"}


def user_headers(user: User) -> dict[str, str]:
    token, _ = create_access_token(user.id, SubjectType.USER)
    return {"Authorization": f"Bearer {token}"}


API = "/api/v1"


@pytest.fixture(scope="session")
def synthetic_dta(tmp_path_factory) -> str:
    from scripts.make_synthetic_dta import write_synthetic_dta

    directory = tmp_path_factory.mktemp("dta")
    path = write_synthetic_dta(directory / "synthetic_census.dta", person_rows=600)
    return str(path)


@pytest.fixture
def survey_admin(admin_factory):
    from app.models.enums import AdminRole

    return admin_factory(role=AdminRole.DATA_ANALYST)


def upload_survey(client, headers, path, *, name="Census 2022", year=2022, keep_file=False):
    with open(path, "rb") as handle:
        return client.post(
            f"{API}/admin/surveys/upload",
            headers=headers,
            files={"file": ("census.dta", handle, "application/octet-stream")},
            data={"name": name, "year": str(year), "keep_file": str(keep_file).lower()},
        )


def confirm_mapping(client, headers, job_id, updates=None):
    return client.post(
        f"{API}/admin/jobs/{job_id}/mapping",
        headers=headers,
        json={"updates": updates or []},
    )
