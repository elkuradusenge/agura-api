"""Audit logging for administrator actions."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.enums import ActorType


def record(
    db: Session,
    *,
    action: str,
    entity_type: str,
    actor_type: ActorType = ActorType.ADMIN,
    actor_id: uuid.UUID | None = None,
    actor_label: str | None = None,
    entity_id: str | uuid.UUID | None = None,
    payload: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> AuditLog:
    entry = AuditLog(
        actor_type=actor_type,
        actor_id=actor_id,
        actor_label=actor_label,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        payload=payload or {},
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(entry)
    db.flush()
    return entry
