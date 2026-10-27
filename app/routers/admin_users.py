"""Administrator management of farmer accounts."""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select

from app.core.deps import DbSession, client_context, require_permissions
from app.core.errors import not_found
from app.core.pagination import Page, PageParamsDep, build_page
from app.core.permissions import Permission
from app.core.security import hash_password
from app.models.admin import Admin
from app.models.enums import SubjectType, UserStatus
from app.models.user import User
from app.schemas.user import AdminUserDetail, AdminUserListItem
from app.services import audit, auth_service

router = APIRouter(prefix="/admin/users", tags=["admin.users"])

ReadAdmin = Annotated[Admin, Depends(require_permissions(Permission.USERS_READ))]
WriteAdmin = Annotated[Admin, Depends(require_permissions(Permission.USERS_WRITE))]

SORTABLE = {
    "created_at": User.created_at,
    "phone": User.phone,
    "last_login_at": User.last_login_at,
    "full_name": User.full_name,
}


class AdminPasswordResetResponse(BaseModel):
    temporary_password: str
    message: str = "Share this temporary password with the farmer"


def _get_user(db, user_id: uuid.UUID) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise not_found("user")
    return user


@router.get("", response_model=Page[AdminUserListItem], summary="List farmers")
def list_users(
    params: PageParamsDep,
    db: DbSession,
    admin: ReadAdmin,
    q: Annotated[str | None, Query(max_length=120)] = None,
    status_filter: Annotated[UserStatus | None, Query(alias="status")] = None,
    province: Annotated[str | None, Query(max_length=80)] = None,
) -> dict:
    query = select(User)
    if q:
        pattern = f"%{q.strip().lower()}%"
        query = query.where(
            or_(func.lower(User.phone).like(pattern), func.lower(User.full_name).like(pattern))
        )
    if status_filter is not None:
        query = query.where(User.status == status_filter)
    else:
        query = query.where(User.status != UserStatus.DELETED)
    if province:
        query = query.where(User.province == province)

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0

    column = SORTABLE.get(params.sort or "created_at", User.created_at)
    ordering = column.asc() if params.order == "asc" else column.desc()
    rows = db.scalars(query.order_by(ordering).offset(params.offset).limit(params.page_size))
    items = [AdminUserListItem.model_validate(row) for row in rows]
    return build_page(items, total, params)


@router.get("/{user_id}", response_model=AdminUserDetail, summary="Read one farmer")
def get_user(user_id: uuid.UUID, db: DbSession, admin: ReadAdmin) -> AdminUserDetail:
    return AdminUserDetail.model_validate(_get_user(db, user_id))


@router.post("/{user_id}/suspend", response_model=AdminUserDetail, summary="Suspend a farmer")
def suspend_user(
    user_id: uuid.UUID, db: DbSession, admin: WriteAdmin, context: dict = Depends(client_context)
) -> AdminUserDetail:
    user = _get_user(db, user_id)
    user.status = UserStatus.SUSPENDED
    user.suspended_at = datetime.now(UTC)
    auth_service.revoke_all_for_subject(db, SubjectType.USER, user.id)
    audit.record(
        db,
        action="user.suspend",
        entity_type="user",
        actor_id=admin.id,
        actor_label=admin.email or admin.name,
        entity_id=user.id,
        payload={"phone": user.phone},
        ip_address=context["ip_address"],
        user_agent=context["user_agent"],
    )
    db.commit()
    db.refresh(user)
    return AdminUserDetail.model_validate(user)


@router.post("/{user_id}/reactivate", response_model=AdminUserDetail, summary="Reactivate a farmer")
def reactivate_user(
    user_id: uuid.UUID, db: DbSession, admin: WriteAdmin, context: dict = Depends(client_context)
) -> AdminUserDetail:
    user = _get_user(db, user_id)
    user.status = UserStatus.ACTIVE
    user.suspended_at = None
    audit.record(
        db,
        action="user.reactivate",
        entity_type="user",
        actor_id=admin.id,
        actor_label=admin.email or admin.name,
        entity_id=user.id,
        payload={"phone": user.phone},
        ip_address=context["ip_address"],
        user_agent=context["user_agent"],
    )
    db.commit()
    db.refresh(user)
    return AdminUserDetail.model_validate(user)


@router.post(
    "/{user_id}/reset-password",
    response_model=AdminPasswordResetResponse,
    summary="Issue a temporary password",
)
def reset_password(
    user_id: uuid.UUID, db: DbSession, admin: WriteAdmin, context: dict = Depends(client_context)
) -> AdminPasswordResetResponse:
    user = _get_user(db, user_id)
    temporary_password = secrets.token_urlsafe(9)
    user.password_hash = hash_password(temporary_password)
    auth_service.revoke_all_for_subject(db, SubjectType.USER, user.id)
    audit.record(
        db,
        action="user.reset_password",
        entity_type="user",
        actor_id=admin.id,
        actor_label=admin.email or admin.name,
        entity_id=user.id,
        payload={"phone": user.phone},
        ip_address=context["ip_address"],
        user_agent=context["user_agent"],
    )
    db.commit()
    return AdminPasswordResetResponse(temporary_password=temporary_password)
