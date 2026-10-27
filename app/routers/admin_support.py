"""Administrator support, monitoring, message templates and settings."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from app.config import get_settings
from app.core.deps import DbSession, client_context, require_permissions
from app.core.errors import not_found, unprocessable
from app.core.pagination import PageParamsDep, build_page
from app.core.permissions import Permission
from app.models.admin import Admin
from app.models.audit_log import AuditLog
from app.models.enums import (
    FundingStatus,
    ProductStatus,
    SmsStatus,
    SpecialistRequestStatus,
    TaskInstanceStatus,
    UserStatus,
)
from app.models.farming import (
    FundingInterest,
    Harvest,
    MessageTemplate,
    Notification,
    Planting,
    SmsLog,
    SpecialistRequest,
    TaskInstance,
)
from app.models.product import Product
from app.models.user import User
from app.scheduler.reminders import run_once
from app.schemas.farming import (
    FundingInterestRead,
    FundingInterestUpdate,
    MessageTemplateRead,
    MessageTemplateWrite,
    ReferToBrdRequest,
    SpecialistRequestRead,
    SpecialistRequestUpdate,
)
from app.services import audit, settings_service
from app.sms import templates as template_service
from app.ws import broadcaster

router = APIRouter(prefix="/admin", tags=["admin.support"])

ReadSupport = Annotated[Admin, Depends(require_permissions(Permission.SUPPORT_READ))]
WriteSupport = Annotated[Admin, Depends(require_permissions(Permission.SUPPORT_WRITE))]
ReadMonitoring = Annotated[Admin, Depends(require_permissions(Permission.MONITORING_READ))]
WriteTemplates = Annotated[Admin, Depends(require_permissions(Permission.TEMPLATES_WRITE))]
WriteSettings = Annotated[Admin, Depends(require_permissions(Permission.SETTINGS_WRITE))]


def _audit(db, admin, context, action, entity_type, entity_id, payload) -> None:
    audit.record(
        db,
        action=action,
        entity_type=entity_type,
        actor_id=admin.id,
        actor_label=admin.email or admin.name,
        entity_id=entity_id,
        payload=payload,
        ip_address=context["ip_address"],
        user_agent=context["user_agent"],
    )


# Specialist requests


@router.get(
    "/specialist-requests",
    summary="Specialist request board",
)
def list_specialist_requests(
    params: PageParamsDep,
    db: DbSession,
    admin: ReadSupport,
    status_filter: Annotated[SpecialistRequestStatus | None, Query(alias="status")] = None,
) -> dict:
    query = select(SpecialistRequest)
    if status_filter is not None:
        query = query.where(SpecialistRequest.status == status_filter)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(
        query.order_by(SpecialistRequest.created_at.desc())
        .offset(params.offset)
        .limit(params.page_size)
    )
    return build_page([SpecialistRequestRead.model_validate(row) for row in rows], total, params)


@router.patch(
    "/specialist-requests/{request_id}",
    response_model=SpecialistRequestRead,
    summary="Assign, annotate or change the status of a request",
)
def update_specialist_request(
    request_id: uuid.UUID,
    payload: SpecialistRequestUpdate,
    db: DbSession,
    admin: WriteSupport,
    context: Annotated[dict, Depends(client_context)],
) -> SpecialistRequestRead:
    record = db.get(SpecialistRequest, request_id)
    if record is None:
        raise not_found("specialist_request")

    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(record, field, value)

    if payload.status is SpecialistRequestStatus.CONTACTED and record.contacted_at is None:
        record.contacted_at = datetime.now(UTC)
    if payload.status is SpecialistRequestStatus.CLOSED:
        record.closed_at = datetime.now(UTC)

    _audit(
        db, admin, context, "specialist_request.update", "specialist_request", record.id, changes
    )
    db.commit()
    db.refresh(record)

    if payload.status is not None:
        broadcaster.notify_user(
            db,
            record.user_id,
            "specialist_request.updated",
            {"request_id": str(record.id), "status": record.status.value},
        )
    return SpecialistRequestRead.model_validate(record)


# Funding interest


@router.get("/funding-interest", summary="Funding interest table")
def list_funding_interest(
    params: PageParamsDep,
    db: DbSession,
    admin: ReadSupport,
    status_filter: Annotated[FundingStatus | None, Query(alias="status")] = None,
    consent: Annotated[bool | None, Query()] = None,
) -> dict:
    query = select(FundingInterest)
    if status_filter is not None:
        query = query.where(FundingInterest.status == status_filter)
    if consent is not None:
        query = query.where(FundingInterest.consent_to_share.is_(consent))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(
        query.order_by(FundingInterest.created_at.desc())
        .offset(params.offset)
        .limit(params.page_size)
    )
    return build_page([FundingInterestRead.model_validate(row) for row in rows], total, params)


@router.patch(
    "/funding-interest/{interest_id}",
    response_model=FundingInterestRead,
    summary="Update a funding interest record",
)
def update_funding_interest(
    interest_id: uuid.UUID,
    payload: FundingInterestUpdate,
    db: DbSession,
    admin: WriteSupport,
    context: Annotated[dict, Depends(client_context)],
) -> FundingInterestRead:
    record = db.get(FundingInterest, interest_id)
    if record is None:
        raise not_found("funding_interest")
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(record, field, value)
    _audit(db, admin, context, "funding_interest.update", "funding_interest", record.id, changes)
    db.commit()
    db.refresh(record)
    return FundingInterestRead.model_validate(record)


@router.post(
    "/funding-interest/{interest_id}/refer-to-brd",
    response_model=FundingInterestRead,
    summary="Refer a farmer to BRD, only with their consent",
)
def refer_to_brd(
    interest_id: uuid.UUID,
    payload: ReferToBrdRequest,
    db: DbSession,
    admin: WriteSupport,
    context: Annotated[dict, Depends(client_context)],
) -> FundingInterestRead:
    record = db.get(FundingInterest, interest_id)
    if record is None:
        raise not_found("funding_interest")
    if not record.consent_to_share:
        raise unprocessable(
            "funding.no_consent",
            "errors.funding.no_consent",
            "The farmer has not consented to sharing their details with BRD",
        )

    record.status = FundingStatus.REFERRED_TO_BRD
    record.referred_to_brd_at = datetime.now(UTC)
    record.brd_reference = payload.brd_reference
    if payload.note:
        record.admin_notes = payload.note

    _audit(
        db,
        admin,
        context,
        "funding_interest.refer_to_brd",
        "funding_interest",
        record.id,
        {"brd_reference": payload.brd_reference},
    )
    db.commit()
    db.refresh(record)

    broadcaster.notify_user(
        db,
        record.user_id,
        "funding_interest.updated",
        {"funding_interest_id": str(record.id), "status": record.status.value},
    )
    return FundingInterestRead.model_validate(record)


# Message templates


@router.get("/message-templates", summary="List message templates")
def list_templates(
    db: DbSession,
    admin: ReadSupport,
    key: Annotated[str | None, Query(max_length=120)] = None,
    language: Annotated[str | None, Query(pattern="^(rw|en|fr)$")] = None,
) -> dict:
    query = select(MessageTemplate)
    if key:
        query = query.where(MessageTemplate.key == key)
    if language:
        query = query.where(MessageTemplate.language == language)
    rows = db.scalars(query.order_by(MessageTemplate.key, MessageTemplate.language)).all()
    return {
        "items": [MessageTemplateRead.model_validate(row) for row in rows],
        "available_variables": sorted(
            {variable for row in rows for variable in (row.variables or [])}
        ),
    }


@router.put(
    "/message-templates/{key}/{language}",
    response_model=MessageTemplateRead,
    summary="Edit a message template",
)
def upsert_template(
    key: str,
    language: str,
    payload: MessageTemplateWrite,
    db: DbSession,
    admin: WriteTemplates,
    context: Annotated[dict, Depends(client_context)],
) -> MessageTemplateRead:
    if language not in {"rw", "en", "fr"}:
        raise unprocessable(
            "templates.invalid_language",
            "errors.templates.invalid_language",
            "Language must be rw, en or fr",
        )
    if payload.sms_body and len(payload.sms_body) > 160:
        raise unprocessable(
            "templates.sms_too_long",
            "errors.templates.sms_too_long",
            "SMS templates must fit in 160 characters",
            {"length": len(payload.sms_body)},
        )

    template = template_service.get_template(db, key, language)
    if template is None:
        template = MessageTemplate(key=key, language=language, body=payload.body)
        db.add(template)
    template.body = payload.body
    template.sms_body = payload.sms_body
    template.variables = payload.variables

    _audit(
        db, admin, context, "message_template.update", "message_template", f"{key}:{language}", {}
    )
    db.commit()
    db.refresh(template)
    return MessageTemplateRead.model_validate(template)


@router.post("/message-templates/seed", summary="Restore the default templates")
def seed_templates(
    db: DbSession,
    admin: WriteTemplates,
    context: Annotated[dict, Depends(client_context)],
) -> dict:
    written = template_service.seed_templates(db)
    _audit(
        db, admin, context, "message_template.seed", "message_template", "all", {"written": written}
    )
    db.commit()
    return {"written": written}


# Monitoring


@router.get("/monitoring/overview", summary="Platform overview")
def monitoring_overview(db: DbSession, admin: ReadMonitoring) -> dict:
    total_users = db.scalar(select(func.count()).select_from(User)) or 0
    active_users = (
        db.scalar(select(func.count()).select_from(User).where(User.status == UserStatus.ACTIVE))
        or 0
    )
    plantings = db.scalar(select(func.count()).select_from(Planting)) or 0
    active_plantings = (
        db.scalar(select(func.count()).select_from(Planting).where(Planting.status == "active"))
        or 0
    )
    tasks = db.scalar(select(func.count()).select_from(TaskInstance)) or 0
    done_tasks = (
        db.scalar(
            select(func.count())
            .select_from(TaskInstance)
            .where(TaskInstance.status == TaskInstanceStatus.DONE)
        )
        or 0
    )
    reminders = db.scalar(select(func.count()).select_from(Notification)) or 0
    sms_sent = (
        db.scalar(select(func.count()).select_from(SmsLog).where(SmsLog.status == SmsStatus.SENT))
        or 0
    )
    sms_failed = (
        db.scalar(select(func.count()).select_from(SmsLog).where(SmsLog.status == SmsStatus.FAILED))
        or 0
    )
    open_requests = (
        db.scalar(
            select(func.count())
            .select_from(SpecialistRequest)
            .where(SpecialistRequest.status != SpecialistRequestStatus.CLOSED)
        )
        or 0
    )
    trackable_products = (
        db.scalar(
            select(func.count())
            .select_from(Product)
            .where(Product.status == ProductStatus.TRACKABLE)
        )
        or 0
    )
    harvests = db.scalar(select(func.count()).select_from(Harvest)) or 0

    return {
        "users": {"total": total_users, "active": active_users},
        "plantings": {"total": plantings, "active": active_plantings},
        "tasks": {
            "total": tasks,
            "done": done_tasks,
            "completion_rate": round(done_tasks / tasks, 4) if tasks else None,
        },
        "reminders": {"created": reminders, "sms_sent": sms_sent, "sms_failed": sms_failed},
        "specialist_requests": {"open": open_requests},
        "products": {"trackable": trackable_products},
        "harvests": {"total": harvests},
    }


@router.get("/monitoring/funnel", summary="Sign up to harvest funnel")
def monitoring_funnel(db: DbSession, admin: ReadMonitoring) -> dict:
    signed_up = db.scalar(select(func.count()).select_from(User)) or 0
    planted = db.scalar(select(func.count(func.distinct(Planting.user_id)))) or 0
    completed_task = (
        db.scalar(
            select(func.count(func.distinct(TaskInstance.user_id))).where(
                TaskInstance.status == TaskInstanceStatus.DONE
            )
        )
        or 0
    )
    harvested = db.scalar(select(func.count(func.distinct(Harvest.user_id)))) or 0
    return {
        "stages": [
            {"key": "signed_up", "count": signed_up},
            {"key": "planted", "count": planted},
            {"key": "completed_first_task", "count": completed_task},
            {"key": "harvested", "count": harvested},
        ]
    }


@router.get("/monitoring/signups", summary="Daily sign ups for the last 30 days")
def monitoring_signups(db: DbSession, admin: ReadMonitoring) -> dict:
    since = datetime.now(UTC) - timedelta(days=30)
    rows = db.execute(
        select(func.date(User.created_at), func.count())
        .where(User.created_at >= since)
        .group_by(func.date(User.created_at))
        .order_by(func.date(User.created_at))
    ).all()
    return {"series": [{"date": str(day), "count": count} for day, count in rows]}


@router.get("/monitoring/sms-failures", summary="Failed SMS deliveries")
def sms_failures(
    params: PageParamsDep,
    db: DbSession,
    admin: ReadMonitoring,
    status_filter: Annotated[SmsStatus, Query(alias="status")] = SmsStatus.FAILED,
) -> dict:
    query = select(SmsLog).where(SmsLog.status == status_filter)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(
        query.order_by(SmsLog.created_at.desc()).offset(params.offset).limit(params.page_size)
    )
    items = [
        {
            "id": str(row.id),
            "phone": row.phone,
            "status": row.status.value,
            "attempts": row.attempts,
            "error": row.error,
            "provider": row.provider,
            "template_key": row.template_key,
            "created_at": row.created_at,
        }
        for row in rows
    ]
    return build_page(items, total, params)


@router.post("/monitoring/run-reminders", summary="Run the reminder pass now")
def run_reminders(
    db: DbSession,
    admin: ReadMonitoring,
    context: Annotated[dict, Depends(client_context)],
) -> dict:
    result = run_once(db, get_settings())
    _audit(db, admin, context, "reminders.run", "scheduler", "manual", result.as_dict())
    db.commit()
    return result.as_dict()


# Settings


@router.get("/settings", summary="Read application settings")
def list_settings(db: DbSession, admin: ReadSupport) -> dict:
    return {"items": settings_service.all_settings(db)}


@router.put("/settings/{key}", summary="Update one application setting")
def update_setting(
    key: str,
    payload: dict,
    db: DbSession,
    admin: WriteSettings,
    context: Annotated[dict, Depends(client_context)],
) -> dict:
    if "value" not in payload:
        raise unprocessable(
            "settings.missing_value",
            "errors.settings.missing_value",
            "The request must include a value",
        )
    if key == "privacy.small_cell_threshold":
        value = payload["value"]
        if not isinstance(value, int) or value < 1:
            raise unprocessable(
                "settings.invalid_threshold",
                "errors.settings.invalid_threshold",
                "The suppression threshold must be a whole number of at least 1",
            )
    settings_service.set_value(db, key, payload["value"], updated_by_id=admin.id)
    _audit(db, admin, context, "settings.update", "app_setting", key, {"value": payload["value"]})
    db.commit()
    return {"key": key, "value": payload["value"]}


@router.get("/audit-log/export", summary="Audit log as a flat list for export")
def audit_export(db: DbSession, admin: ReadSupport) -> dict:
    rows = db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(5000)).all()
    return {
        "items": [
            {
                "created_at": row.created_at,
                "actor_label": row.actor_label,
                "action": row.action,
                "entity_type": row.entity_type,
                "entity_id": row.entity_id,
                "payload": row.payload,
            }
            for row in rows
        ]
    }
