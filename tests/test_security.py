"""Unit tests for security helpers, permissions and settings defaults."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.config import get_settings
from app.core.errors import ApiError
from app.core.permissions import Permission, has_permissions, permissions_for
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    normalize_phone,
    verify_password,
)
from app.models.enums import AdminRole, SubjectType
from app.services import settings_service


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0788123456", "+250788123456"),
        ("+250788123456", "+250788123456"),
        ("250788123456", "+250788123456"),
        ("0788 123 456", "+250788123456"),
        ("0788-123-456", "+250788123456"),
        ("(0788) 123456", "+250788123456"),
        ("00250788123456", "+250788123456"),
        ("788123456", "+250788123456"),
    ],
)
def test_normalize_phone_accepts_every_common_format(raw, expected):
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", ["12345", "", "07881234567", "07881234", "+255788123456"])
def test_normalize_phone_rejects_invalid_numbers(raw):
    with pytest.raises(ApiError) as error:
        normalize_phone(raw)
    assert error.value.code == "auth.invalid_phone"


def test_password_hash_round_trip():
    password_hash = hash_password("FarmerPass123!")
    assert password_hash != "FarmerPass123!"
    assert verify_password("FarmerPass123!", password_hash)
    assert not verify_password("wrong", password_hash)


def test_access_token_carries_subject_and_role():
    subject_id = uuid.uuid4()
    token, expires_at = create_access_token(subject_id, SubjectType.ADMIN, "super_admin")
    payload = decode_access_token(token)
    assert payload["sub"] == str(subject_id)
    assert payload["act"] == "admin"
    assert payload["role"] == "super_admin"
    assert payload["typ"] == "access"
    assert expires_at > datetime.now(UTC)


def test_expired_access_token_reports_a_specific_code():
    settings = get_settings()
    expired = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "act": "user",
            "typ": "access",
            "iss": settings.jwt_issuer,
            "exp": int((datetime.now(UTC) - timedelta(minutes=1)).timestamp()),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    with pytest.raises(ApiError) as error:
        decode_access_token(expired)
    assert error.value.code == "auth.token_expired"


def test_token_signed_with_another_secret_is_rejected():
    token = jwt.encode(
        {"sub": str(uuid.uuid4()), "act": "user", "typ": "access", "exp": 9999999999},
        "not-the-app-secret",
        algorithm="HS256",
    )
    with pytest.raises(ApiError) as error:
        decode_access_token(token)
    assert error.value.code == "auth.invalid_token"


def test_role_permission_matrix():
    assert has_permissions(AdminRole.SUPER_ADMIN, list(Permission))
    assert Permission.USERS_READ in permissions_for(AdminRole.SUPPORT_AGENT)
    assert Permission.USERS_WRITE not in permissions_for(AdminRole.SUPPORT_AGENT)
    assert Permission.PRODUCTS_WRITE in permissions_for(AdminRole.CONTENT_EDITOR)
    assert Permission.SURVEYS_WRITE in permissions_for(AdminRole.DATA_ANALYST)
    assert Permission.AUDIT_READ not in permissions_for(AdminRole.DATA_ANALYST)
    assert not has_permissions(AdminRole.CONTENT_EDITOR, [Permission.USERS_READ])


def test_settings_defaults_are_seeded_once(db):
    created = settings_service.seed_defaults(db)
    assert created == len(settings_service.DEFAULT_SETTINGS)
    assert settings_service.seed_defaults(db) == 0
    assert settings_service.get_value(db, "privacy.small_cell_threshold") == 10
    assert settings_service.small_cell_threshold(db) == 10


def test_settings_can_be_overwritten(db):
    settings_service.seed_defaults(db)
    settings_service.set_value(db, "privacy.small_cell_threshold", 25)
    assert settings_service.small_cell_threshold(db) == 25
