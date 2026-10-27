"""Farmer accounts."""

from __future__ import annotations

from datetime import datetime, time

from sqlalchemy import Boolean, DateTime, Integer, String, Time
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, value_enum
from app.models.enums import Language, UserStatus


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "users"

    phone: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(120))
    language: Mapped[Language] = mapped_column(
        value_enum(Language, "language", 4),
        nullable=False,
        default=Language.RW,
        server_default=Language.RW.value,
    )
    province: Mapped[str | None] = mapped_column(String(80))
    district: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[UserStatus] = mapped_column(
        value_enum(UserStatus, "user_status", 16),
        nullable=False,
        default=UserStatus.ACTIVE,
        server_default=UserStatus.ACTIVE.value,
        index=True,
    )
    phone_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    data_saver: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    quiet_hours_start: Mapped[time] = mapped_column(
        Time, nullable=False, default=time(21, 0), server_default="21:00"
    )
    quiet_hours_end: Mapped[time] = mapped_column(
        Time, nullable=False, default=time(5, 0), server_default="05:00"
    )
    reminder_morning_time: Mapped[time] = mapped_column(
        Time, nullable=False, default=time(7, 0), server_default="07:00"
    )
    reminder_evening_time: Mapped[time] = mapped_column(
        Time, nullable=False, default=time(18, 0), server_default="18:00"
    )
    daily_message_limit: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default="3"
    )
    message_opt_in: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
