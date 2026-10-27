"""FastAPI dependencies for authentication and authorisation."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.errors import forbidden, invalid_token, not_authenticated
from app.core.permissions import Permission, has_permissions
from app.core.security import decode_access_token
from app.db import get_db
from app.models.admin import Admin
from app.models.enums import AdminStatus, SubjectType, UserStatus
from app.models.user import User

bearer_scheme = HTTPBearer(auto_error=False)

DbSession = Annotated[Session, Depends(get_db)]
Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)]


def _token_payload(credentials: HTTPAuthorizationCredentials | None) -> dict:
    if credentials is None or not credentials.credentials:
        raise not_authenticated()
    return decode_access_token(credentials.credentials)


def get_current_user(credentials: Credentials, db: DbSession) -> User:
    payload = _token_payload(credentials)
    if payload.get("act") != SubjectType.USER.value:
        raise invalid_token()
    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise invalid_token() from exc

    user = db.get(User, user_id)
    if user is None or user.status == UserStatus.DELETED:
        raise not_authenticated()
    if user.status == UserStatus.SUSPENDED:
        raise forbidden("This account is suspended")
    return user


def get_current_admin(credentials: Credentials, db: DbSession) -> Admin:
    payload = _token_payload(credentials)
    if payload.get("act") != SubjectType.ADMIN.value:
        raise invalid_token()
    try:
        admin_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise invalid_token() from exc

    admin = db.get(Admin, admin_id)
    if admin is None:
        raise not_authenticated()
    if admin.status != AdminStatus.ACTIVE:
        raise forbidden("This administrator account is not active")
    return admin


CurrentUser = Annotated[User, Depends(get_current_user)]
CurrentAdmin = Annotated[Admin, Depends(get_current_admin)]


def require_permissions(
    *permissions: Permission,
) -> Callable[[Admin], Admin]:
    """Dependency factory: the admin must hold every listed permission."""

    def _dependency(admin: Annotated[Admin, Depends(get_current_admin)]) -> Admin:
        if not has_permissions(admin.role, permissions):
            raise forbidden()
        return admin

    return _dependency


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def user_agent(request: Request) -> str | None:
    agent = request.headers.get("user-agent")
    return agent[:512] if agent else None


def client_context(request: Request) -> dict[str, str | None]:
    return {"ip_address": client_ip(request), "user_agent": user_agent(request)}
