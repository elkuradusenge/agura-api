"""The signed in farmer's own profile."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter

from app.core.deps import CurrentUser, DbSession
from app.core.security import hash_password
from app.models.enums import SubjectType, UserStatus
from app.schemas.auth import AccountDeletedResponse, ChangePasswordRequest
from app.schemas.common import MessageResponse
from app.schemas.user import UserRead, UserUpdate
from app.services import auth_service

router = APIRouter(prefix="/me", tags=["me"])


@router.get("", response_model=UserRead, summary="Read my profile")
def read_me(user: CurrentUser) -> UserRead:
    return UserRead.model_validate(user)


@router.patch("", response_model=UserRead, summary="Update language, location and reminders")
def update_me(payload: UserUpdate, user: CurrentUser, db: DbSession) -> UserRead:
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    db.commit()
    db.refresh(user)
    return UserRead.model_validate(user)


@router.post("/password", response_model=MessageResponse, summary="Change my password")
def change_password(
    payload: ChangePasswordRequest, user: CurrentUser, db: DbSession
) -> MessageResponse:
    auth_service.change_password(db, user, payload.current_password, payload.new_password)
    return MessageResponse(message="password_changed")


@router.delete("", response_model=AccountDeletedResponse, summary="Delete my account and data")
def delete_me(user: CurrentUser, db: DbSession) -> AccountDeletedResponse:
    """Anonymise the account and revoke every session.

    Farming records belonging to the account are removed in the milestone that
    introduces them. The phone number is replaced so the person may register again.
    """
    now = datetime.now(UTC)
    auth_service.revoke_all_for_subject(db, SubjectType.USER, user.id)
    user.status = UserStatus.DELETED
    user.deleted_at = now
    user.phone = f"deleted-{user.id.hex[:12]}"
    user.full_name = None
    user.province = None
    user.district = None
    user.message_opt_in = False
    user.password_hash = hash_password(f"deleted-{user.id.hex}")
    db.commit()
    return AccountDeletedResponse(deleted_at=now)
