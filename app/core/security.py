"""Password hashing, phone normalisation and JWT helpers."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.config import Settings, get_settings
from app.core.errors import ApiError, invalid_token, unprocessable
from app.models.enums import SubjectType

_PASSWORD_HASHER = PasswordHasher()
_RWANDA_MOBILE = re.compile(r"^\+2507[2389]\d{7}$")


def hash_password(password: str) -> str:
    return _PASSWORD_HASHER.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _PASSWORD_HASHER.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    try:
        return _PASSWORD_HASHER.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def validate_password_strength(password: str, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    if len(password) < settings.password_min_length:
        raise unprocessable(
            "auth.weak_password",
            "errors.auth.weak_password",
            f"Password must be at least {settings.password_min_length} characters",
        )
    if len(password) > 128:
        raise unprocessable(
            "auth.weak_password",
            "errors.auth.weak_password",
            "Password must be at most 128 characters",
        )


def normalize_phone(raw: str) -> str:
    """Normalise a Rwandan mobile number to the +2507XXXXXXXX form."""
    if raw is None:
        raise unprocessable("auth.invalid_phone", "errors.auth.invalid_phone", "Phone is required")
    cleaned = re.sub(r"[\s\-().]", "", str(raw))
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    if cleaned.startswith("+"):
        candidate = cleaned
    elif cleaned.startswith("250"):
        candidate = "+" + cleaned
    elif cleaned.startswith("0"):
        candidate = "+250" + cleaned[1:]
    elif cleaned.startswith("7"):
        candidate = "+250" + cleaned
    else:
        candidate = "+" + cleaned
    if not _RWANDA_MOBILE.match(candidate):
        raise unprocessable(
            "auth.invalid_phone",
            "errors.auth.invalid_phone",
            "Enter a Rwandan mobile number such as 0788123456",
        )
    return candidate


def _jwt_settings() -> Settings:
    return get_settings()


def create_access_token(
    subject_id: uuid.UUID,
    subject_type: SubjectType,
    role: str | None = None,
) -> tuple[str, datetime]:
    settings = _jwt_settings()
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=settings.access_token_minutes)
    payload: dict[str, Any] = {
        "sub": str(subject_id),
        "act": subject_type.value,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "iss": settings.jwt_issuer,
        "jti": uuid.uuid4().hex,
        "typ": "access",
    }
    if role:
        payload["role"] = role
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, expires_at


def decode_access_token(token: str) -> dict[str, Any]:
    settings = _jwt_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
        )
    except jwt.ExpiredSignatureError as exc:
        raise ApiError(
            401, "auth.token_expired", "errors.auth.token_expired", "The session has expired"
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise invalid_token() from exc
    if payload.get("typ") != "access":
        raise invalid_token()
    return payload


def new_refresh_token() -> tuple[str, str]:
    """Return the raw token for the client and the hash to store."""
    raw = secrets.token_urlsafe(48)
    return raw, hash_token(raw)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def refresh_token_expiry() -> datetime:
    return datetime.now(UTC) + timedelta(days=_jwt_settings().refresh_token_days)


def generate_numeric_code(length: int = 6) -> str:
    return "".join(secrets.choice("0123456789") for _ in range(length))


def hash_code(code: str) -> str:
    pepper = _jwt_settings().jwt_secret.encode("utf-8")
    return hmac.new(pepper, code.encode("utf-8"), hashlib.sha256).hexdigest()
