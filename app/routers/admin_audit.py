"""Read the administrator audit trail."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from app.core.deps import DbSession, require_permissions
from app.core.pagination import Page, PageParamsDep, build_page
from app.core.permissions import Permission
from app.models.admin import Admin
from app.models.audit_log import AuditLog
from app.schemas.audit import AuditLogRead

router = APIRouter(prefix="/admin/audit-log", tags=["admin.audit"])

ReadAudit = Annotated[Admin, Depends(require_permissions(Permission.AUDIT_READ))]

SORTABLE = {"created_at": AuditLog.created_at, "action": AuditLog.action}


@router.get("", response_model=Page[AuditLogRead], summary="List audit log entries")
def list_audit_log(
    params: PageParamsDep,
    db: DbSession,
    admin: ReadAudit,
    action: Annotated[str | None, Query(max_length=80)] = None,
    entity_type: Annotated[str | None, Query(max_length=80)] = None,
    actor_id: Annotated[str | None, Query(max_length=36)] = None,
) -> dict:
    query = select(AuditLog)
    if action:
        query = query.where(AuditLog.action == action)
    if entity_type:
        query = query.where(AuditLog.entity_type == entity_type)
    if actor_id:
        query = query.where(AuditLog.actor_id == actor_id)

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    column = SORTABLE.get(params.sort or "created_at", AuditLog.created_at)
    ordering = column.asc() if params.order == "asc" else column.desc()
    rows = db.scalars(query.order_by(ordering).offset(params.offset).limit(params.page_size))
    items = [AuditLogRead.model_validate(row) for row in rows]
    return build_page(items, total, params)
