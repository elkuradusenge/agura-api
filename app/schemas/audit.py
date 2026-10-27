"""Audit log schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models.enums import ActorType


class AuditLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    actor_type: ActorType
    actor_id: uuid.UUID | None = None
    actor_label: str | None = None
    action: str
    entity_type: str
    entity_id: str | None = None
    payload: dict[str, Any]
    ip_address: str | None = None
    created_at: datetime
