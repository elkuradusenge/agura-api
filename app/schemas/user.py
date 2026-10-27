"""Farmer account schemas."""

from __future__ import annotations

import uuid
from datetime import datetime, time

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import Language, UserStatus


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    phone: str
    full_name: str | None = None
    language: Language
    province: str | None = None
    district: str | None = None
    status: UserStatus
    phone_verified: bool
    data_saver: bool
    quiet_hours_start: time
    quiet_hours_end: time
    reminder_morning_time: time
    reminder_evening_time: time
    message_opt_in: bool
    created_at: datetime


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=120)
    language: Language | None = None
    province: str | None = Field(default=None, max_length=80)
    district: str | None = Field(default=None, max_length=80)
    quiet_hours_start: time | None = None
    quiet_hours_end: time | None = None
    reminder_morning_time: time | None = None
    reminder_evening_time: time | None = None
    data_saver: bool | None = None
    message_opt_in: bool | None = None


class AdminUserListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    phone: str
    full_name: str | None = None
    language: Language
    province: str | None = None
    district: str | None = None
    status: UserStatus
    created_at: datetime
    last_login_at: datetime | None = None


class AdminUserDetail(AdminUserListItem):
    phone_verified: bool
    data_saver: bool
    message_opt_in: bool
    suspended_at: datetime | None = None
