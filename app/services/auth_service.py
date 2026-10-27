"""Registration, sign in, token rotation and password reset."""

from __future__ import annotations

import hmac
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.errors import (
    conflict,
    forbidden,
    invalid_credentials,
    invalid_token,
    unprocessable,
)
from app.core.security import (
    create_access_token,
    generate_numeric_code,
    hash_code,
    hash_password,
    hash_token,
    new_refresh_token,
    normalize_phone,
    password_needs_rehash,
    refresh_token_expiry,
    validate_password_strength,
    verify_password,
)
from app.models.admin import Admin
from app.models.enums import AdminRole, AdminStatus, SubjectType, UserStatus
from app.models.password_reset_code import PasswordResetCode
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.schemas.auth import RegisterRequest
from app.services.sms import send_sms

logger = logging.getLogger("agura.auth")

MAX_RESET_ATTEMPTS = 5
# Verified against when an account is missing so response time does not leak existence.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def _now() -> datetime:
    return datetime.now(UTC)


def _issue_tokens(
    db: Session,
    subject_id: uuid.UUID,
    subject_type: SubjectType,
    role: str | None,
    *,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> tuple[str, str, int]:
    settings = get_settings()
    access_token, _ = create_access_token(subject_id, subject_type, role)
    raw_refresh, refresh_hash = new_refresh_token()
    db.add(
        RefreshToken(
            subject_type=subject_type,
            subject_id=subject_id,
            token_hash=refresh_hash,
            expires_at=refresh_token_expiry(),
            ip_address=ip_address,
            user_agent=user_agent,
        )
    )
    db.flush()
    return access_token, raw_refresh, settings.access_token_minutes * 60


def register_user(db: Session, payload: RegisterRequest) -> tuple[User, str, str, int]:
    settings = get_settings()
    phone = normalize_phone(payload.phone)
    validate_password_strength(payload.password, settings)

    existing = db.scalar(select(User).where(User.phone == phone))
    if existing is not None:
        raise conflict(
            "auth.phone_taken",
            "errors.auth.phone_taken",
            "An account already exists for this phone number",
        )

    user = User(
        phone=phone,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name.strip() if payload.full_name else None,
        language=payload.language,
    )
    db.add(user)
    db.flush()
    access_token, refresh_token, expires_in = _issue_tokens(db, user.id, SubjectType.USER, None)
    db.commit()
    db.refresh(user)
    return user, access_token, refresh_token, expires_in


def authenticate_user(db: Session, phone: str, password: str) -> tuple[User, str, str, int]:
    normalised = normalize_phone(phone)
    user = db.scalar(select(User).where(User.phone == normalised))
    if user is None or user.status == UserStatus.DELETED:
        verify_password(password, _DUMMY_HASH)
        raise invalid_credentials()
    if not verify_password(password, user.password_hash):
        raise invalid_credentials()
    if user.status == UserStatus.SUSPENDED:
        raise forbidden("This account is suspended")

    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.last_login_at = _now()
    access_token, refresh_token, expires_in = _issue_tokens(db, user.id, SubjectType.USER, None)
    db.commit()
    db.refresh(user)
    return user, access_token, refresh_token, expires_in


def _find_admin_by_identifier(db: Session, identifier: str) -> Admin | None:
    identifier = identifier.strip()
    if "@" in identifier:
        return db.scalar(select(Admin).where(Admin.email.ilike(identifier.lower())))
    try:
        phone = normalize_phone(identifier)
    except Exception:
        return None
    return db.scalar(select(Admin).where(Admin.phone == phone))


def authenticate_admin(db: Session, identifier: str, password: str) -> tuple[Admin, str, str, int]:
    admin = _find_admin_by_identifier(db, identifier)
    if admin is None:
        verify_password(password, _DUMMY_HASH)
        raise invalid_credentials("Email, phone number or password is incorrect")
    if not verify_password(password, admin.password_hash):
        raise invalid_credentials("Email, phone number or password is incorrect")
    if admin.status != AdminStatus.ACTIVE:
        raise forbidden("This administrator account is not active")

    if password_needs_rehash(admin.password_hash):
        admin.password_hash = hash_password(password)
    admin.last_login_at = _now()
    access_token, refresh_token, expires_in = _issue_tokens(
        db, admin.id, SubjectType.ADMIN, admin.role.value
    )
    db.commit()
    db.refresh(admin)
    return admin, access_token, refresh_token, expires_in


def rotate_refresh_token(
    db: Session, raw_token: str, *, ip_address: str | None = None, user_agent: str | None = None
) -> tuple[SubjectType, uuid.UUID, str, str, int]:
    token_hash = hash_token(raw_token)
    record = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    if record is None:
        raise invalid_token()
    if record.revoked_at is not None:
        raise invalid_token()
    if record.expires_at <= _now():
        raise invalid_token("The session has expired")

    role: str | None = None
    if record.subject_type == SubjectType.USER:
        subject = db.get(User, record.subject_id)
        if subject is None or subject.status != UserStatus.ACTIVE:
            raise invalid_token()
    else:
        admin = db.get(Admin, record.subject_id)
        if admin is None or admin.status != AdminStatus.ACTIVE:
            raise invalid_token()
        role = admin.role.value

    record.revoked_at = _now()
    access_token, refresh_token, expires_in = _issue_tokens(
        db,
        record.subject_id,
        record.subject_type,
        role,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.commit()
    return record.subject_type, record.subject_id, access_token, refresh_token, expires_in


def revoke_refresh_token(db: Session, raw_token: str) -> None:
    token_hash = hash_token(raw_token)
    record = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    if record is not None and record.revoked_at is None:
        record.revoked_at = _now()
        db.commit()


def revoke_all_for_subject(db: Session, subject_type: SubjectType, subject_id: uuid.UUID) -> None:
    db.execute(
        update(RefreshToken)
        .where(
            RefreshToken.subject_type == subject_type,
            RefreshToken.subject_id == subject_id,
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=_now())
    )


def request_password_reset(db: Session, phone: str) -> str | None:
    """Create a reset code when the account exists. Never reveals whether it does."""
    settings = get_settings()
    try:
        normalised = normalize_phone(phone)
    except Exception:
        return None

    user = db.scalar(select(User).where(User.phone == normalised))
    if user is None or user.status != UserStatus.ACTIVE:
        return None

    code = generate_numeric_code()
    db.add(
        PasswordResetCode(
            phone=normalised,
            code_hash=hash_code(code),
            expires_at=_now() + timedelta(minutes=settings.password_reset_code_minutes),
        )
    )
    db.commit()

    send_sms(
        normalised,
        f"AGUra: your reset code is {code}. It expires in "
        f"{settings.password_reset_code_minutes} minutes.",
    )
    return code


def confirm_password_reset(db: Session, phone: str, code: str, new_password: str) -> None:
    settings = get_settings()
    normalised = normalize_phone(phone)
    validate_password_strength(new_password, settings)

    record = db.scalar(
        select(PasswordResetCode)
        .where(
            PasswordResetCode.phone == normalised,
            PasswordResetCode.consumed_at.is_(None),
        )
        .order_by(PasswordResetCode.created_at.desc())
        .limit(1)
    )
    if record is None or record.expires_at <= _now() or record.attempts >= MAX_RESET_ATTEMPTS:
        raise unprocessable(
            "auth.reset_code_invalid",
            "errors.auth.reset_code_invalid",
            "The reset code is invalid or has expired",
        )

    record.attempts += 1
    if not hmac.compare_digest(record.code_hash, hash_code(code)):
        db.commit()
        raise unprocessable(
            "auth.reset_code_invalid",
            "errors.auth.reset_code_invalid",
            "The reset code is invalid or has expired",
        )

    user = db.scalar(select(User).where(User.phone == normalised))
    if user is None or user.status == UserStatus.DELETED:
        raise unprocessable(
            "auth.reset_code_invalid",
            "errors.auth.reset_code_invalid",
            "The reset code is invalid or has expired",
        )

    record.consumed_at = _now()
    user.password_hash = hash_password(new_password)
    revoke_all_for_subject(db, SubjectType.USER, user.id)
    db.commit()


def change_password(db: Session, user: User, current_password: str, new_password: str) -> None:
    if not verify_password(current_password, user.password_hash):
        raise invalid_credentials("The current password is incorrect")
    validate_password_strength(new_password)
    user.password_hash = hash_password(new_password)
    revoke_all_for_subject(db, SubjectType.USER, user.id)
    db.commit()


def ensure_bootstrap_admin(db: Session) -> Admin | None:
    """Create the first super administrator from ADMIN_BOOTSTRAP_PASSWORD."""
    settings = get_settings()
    if not settings.admin_bootstrap_password:
        return None
    existing = db.scalar(select(Admin).limit(1))
    if existing is not None:
        return None
    admin = Admin(
        name=settings.admin_bootstrap_name,
        email=settings.admin_bootstrap_email.lower(),
        password_hash=hash_password(settings.admin_bootstrap_password),
        role=AdminRole.SUPER_ADMIN,
        status=AdminStatus.ACTIVE,
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    logger.info("Bootstrapped super administrator %s", admin.email)
    return admin
