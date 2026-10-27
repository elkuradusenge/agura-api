"""Administrator schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.core.permissions import Permission
from app.models.enums import AdminRole, AdminStatus


class AdminRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    email: str | None = None
    phone: str | None = None
    role: AdminRole
    status: AdminStatus
    last_login_at: datetime | None = None
    created_at: datetime


class AdminWithPermissions(AdminRead):
    permissions: list[Permission] = []
