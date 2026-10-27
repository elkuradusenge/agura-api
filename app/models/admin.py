"""Administrator accounts with role based permissions."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, value_enum
from app.models.enums import AdminRole, AdminStatus


class Admin(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "admins"
    __table_args__ = (
        Index(
            "ix_admins_email_lower",
            text("lower(email)"),
            unique=True,
            postgresql_where=text("email IS NOT NULL"),
        ),
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(20), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[AdminRole] = mapped_column(
        value_enum(AdminRole, "admin_role", 32),
        nullable=False,
        default=AdminRole.SUPPORT_AGENT,
        server_default=AdminRole.SUPPORT_AGENT.value,
        index=True,
    )
    status: Mapped[AdminStatus] = mapped_column(
        value_enum(AdminStatus, "admin_status", 16),
        nullable=False,
        default=AdminStatus.ACTIVE,
        server_default=AdminStatus.ACTIVE.value,
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
