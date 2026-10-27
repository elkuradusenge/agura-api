"""Farmer endpoints: products with province context, plantings, tasks, harvests and support."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, or_, select

from app.core.deps import CurrentUser, DbSession
from app.core.errors import not_found, unprocessable
from app.core.pagination import Page, PageParamsDep, build_page
from app.models.analysis import ProvinceFoodSummary
from app.models.enums import (
    FundingStatus,
    Language,
    NotificationType,
    PlantingStatus,
    ProductStatus,
    SpecialistRequestStatus,
    TaskInstanceStatus,
)
from app.models.farming import (
    FundingInterest,
    Harvest,
    Notification,
    Planting,
    SpecialistRequest,
    TaskInstance,
)
from app.models.product import Product
from app.models.survey import Survey
from app.scheduler import tasks as task_service
from app.scheduler.reminders import KIGALI, tasks_due_today
from app.schemas.farming import (
    FarmerProductDetail,
    FundingInterestCreate,
    FundingInterestRead,
    HarvestCreate,
    HarvestRead,
    HarvestSummary,
    NotificationRead,
    PlantingCreate,
    PlantingCreateResponse,
    PlantingDetail,
    PlantingSummary,
    PlantingUpdate,
    ProductRead,
    ProvinceContext,
    SkipRequest,
    SnoozeRequest,
    SpecialistRequestCreate,
    SpecialistRequestRead,
    TaskRead,
    TodayResponse,
)
from app.services import analysis_queries, product_service, settings_service
from app.sms import templates
from app.ws import broadcaster

router = APIRouter(tags=["farmer"])

PRODUCTS = APIRouter(prefix="/products", tags=["farmer.products"])
PLANTINGS = APIRouter(prefix="/plantings", tags=["farmer.plantings"])
TASKS = APIRouter(prefix="/tasks", tags=["farmer.tasks"])
NOTIFICATIONS = APIRouter(prefix="/notifications", tags=["farmer.notifications"])
HARVESTS = APIRouter(prefix="/harvests", tags=["farmer.harvests"])
SPECIALIST = APIRouter(prefix="/specialist-requests", tags=["farmer.help"])
FUNDING = APIRouter(prefix="/funding-interest", tags=["farmer.funding"])


def _product_summary(product: Product) -> dict:
    return {
        "id": str(product.id),
        "name_en": product.name_en,
        "name_rw": product.name_rw,
        "photo_url": product.photo_url,
        "time_to_harvest_min_days": product.time_to_harvest_min_days,
        "time_to_harvest_max_days": product.time_to_harvest_max_days,
    }


def province_context(db, user, product: Product) -> ProvinceContext | None:
    """The province card shown on a product page, taken from the latest analysis."""
    if not user.province:
        return None

    survey = db.scalar(
        select(Survey)
        .where(Survey.deleted_at.is_(None), Survey.status.in_(["ready", "completed"]))
        .order_by(Survey.created_at.desc())
        .limit(1)
    )
    if survey is None:
        return None

    source_name, source_year = analysis_queries.analysis_source(db)
    threshold = settings_service.small_cell_threshold(db)

    if product.food_item_id is None:
        # Nothing is withheld here: this crop simply has no census row, which is
        # a different thing and must not be shown as suppression.
        return ProvinceContext(
            available=False,
            province=user.province,
            source_name=source_name,
            source_year=source_year,
            survey_name=survey.name,
            suppressed=False,
            translation_key="analysis.not_available",
        )

    row = db.execute(
        select(ProvinceFoodSummary).where(
            ProvinceFoodSummary.survey_id == survey.id,
            ProvinceFoodSummary.food_item_id == product.food_item_id,
            ProvinceFoodSummary.level == "province",
            ProvinceFoodSummary.province == user.province,
        )
    ).scalar_one_or_none()

    if row is None:
        return ProvinceContext(
            available=False,
            province=user.province,
            source_name=source_name,
            source_year=source_year,
            survey_name=survey.name,
            suppressed=True,
            translation_key="analysis.not_available",
        )

    if row.households_with_food < threshold:
        return ProvinceContext(
            available=False,
            province=user.province,
            source_name=source_name,
            source_year=source_year,
            survey_name=survey.name,
            suppressed=True,
            translation_key="analysis.fewer_than_threshold",
        )

    total = float(row.weighted_households_total or 0)
    share = float(row.weighted_households_with_food or 0) / total if total else None
    return ProvinceContext(
        available=True,
        province=user.province,
        share=round(share, 4) if share is not None else None,
        suppressed=False,
        source_name=source_name,
        source_year=source_year,
        survey_name=survey.name,
    )


# Products


@PRODUCTS.get("", response_model=Page[ProductRead], summary="Trackable products only")
def list_products(
    params: PageParamsDep,
    db: DbSession,
    user: CurrentUser,
    q: Annotated[str | None, Query(max_length=160)] = None,
) -> dict:
    query = select(Product).where(Product.status == ProductStatus.TRACKABLE)
    if q:
        pattern = f"%{q.strip().lower()}%"
        query = query.where(
            or_(
                func.lower(Product.name_en).like(pattern),
                func.lower(Product.name_rw).like(pattern),
            )
        )
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(Product.name_en).offset(params.offset).limit(params.page_size))
    return build_page([ProductRead.model_validate(row) for row in rows], total, params)


@PRODUCTS.get(
    "/{product_id}",
    response_model=FarmerProductDetail,
    summary="Product detail with province context",
)
def product_detail(product_id: uuid.UUID, db: DbSession, user: CurrentUser) -> FarmerProductDetail:
    product = product_service.get_trackable_product(db, product_id)
    detail = FarmerProductDetail.model_validate(product)
    detail.province_context = province_context(db, user, product)
    return detail


# Plantings


def _planting_summary(db, planting: Planting, product: Product | None) -> PlantingSummary:
    tasks = db.scalars(select(TaskInstance).where(TaskInstance.planting_id == planting.id)).all()
    done = sum(1 for task in tasks if task.status is TaskInstanceStatus.DONE)
    progress = int(round(100 * done / len(tasks))) if tasks else 0

    next_task = db.scalar(
        select(TaskInstance)
        .where(
            TaskInstance.planting_id == planting.id,
            TaskInstance.status.in_([TaskInstanceStatus.PENDING, TaskInstanceStatus.SNOOZED]),
        )
        .order_by(TaskInstance.due_at)
        .limit(1)
    )
    days_left = None
    if planting.expected_harvest_on:
        days_left = (planting.expected_harvest_on - date.today()).days

    return PlantingSummary(
        id=planting.id,
        product_id=planting.product_id,
        product_version=planting.product_version,
        status=planting.status,
        quantity=planting.quantity,
        started_on=planting.started_on,
        expected_harvest_on=planting.expected_harvest_on,
        notes=planting.notes,
        created_at=planting.created_at,
        product_name_en=product.name_en if product else "",
        product_name_rw=product.name_rw if product else None,
        photo_url=product.photo_url if product else None,
        progress_percent=progress,
        days_left=days_left,
        next_task=TaskRead.model_validate(next_task) if next_task else None,
        checklist_snapshot=planting.checklist_snapshot or [],
    )


@PLANTINGS.post(
    "", response_model=PlantingCreateResponse, status_code=201, summary="Plant a product"
)
def create_planting(
    payload: PlantingCreate, db: DbSession, user: CurrentUser
) -> PlantingCreateResponse:
    product = product_service.get_trackable_product(db, payload.product_id)
    started = payload.started_on or datetime.now(KIGALI).date()

    planting = Planting(
        user_id=user.id,
        product_id=product.id,
        product_version=product.version,
        status=PlantingStatus.ACTIVE,
        quantity=payload.quantity,
        started_on=started,
        expected_harvest_on=task_service.expected_harvest_date(product, started),
        checklist_snapshot=[item.model_dump() for item in payload.checklist],
    )
    db.add(planting)
    db.flush()

    task_service.build_task_instances(db, planting, product, user)
    db.commit()
    db.refresh(planting)

    first = task_service.first_tasks(db, planting.id, 3)
    summary = _planting_summary(db, planting, product)

    broadcaster.notify_user(
        db,
        user.id,
        "planting.created",
        {"planting_id": str(planting.id), "product_id": str(product.id)},
    )
    return PlantingCreateResponse(
        planting=summary,
        first_tasks=[TaskRead.model_validate(task) for task in first],
        expected_harvest_on=planting.expected_harvest_on,
    )


@PLANTINGS.get("", response_model=Page[PlantingSummary], summary="My plantings")
def list_plantings(
    params: PageParamsDep,
    db: DbSession,
    user: CurrentUser,
    status_filter: Annotated[PlantingStatus | None, Query(alias="status")] = None,
) -> dict:
    query = select(Planting).where(Planting.user_id == user.id)
    if status_filter is not None:
        query = query.where(Planting.status == status_filter)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(
        query.order_by(Planting.created_at.desc()).offset(params.offset).limit(params.page_size)
    ).all()
    products = {
        product.id: product
        for product in db.scalars(
            select(Product).where(Product.id.in_([row.product_id for row in rows] or [None]))
        )
    }
    items = [_planting_summary(db, row, products.get(row.product_id)) for row in rows]
    return build_page(items, total, params)


@PLANTINGS.get("/{planting_id}", response_model=PlantingDetail, summary="One planting")
def get_planting(planting_id: uuid.UUID, db: DbSession, user: CurrentUser) -> PlantingDetail:
    planting = db.get(Planting, planting_id)
    if planting is None or planting.user_id != user.id:
        raise not_found("planting")
    product = db.get(Product, planting.product_id)
    summary = _planting_summary(db, planting, product)
    tasks = db.scalars(
        select(TaskInstance)
        .where(TaskInstance.planting_id == planting.id)
        .order_by(TaskInstance.due_at)
    ).all()
    harvests = db.scalars(
        select(Harvest)
        .where(Harvest.planting_id == planting.id)
        .order_by(Harvest.harvested_on.desc())
    ).all()
    return PlantingDetail(
        **summary.model_dump(),
        tasks=[TaskRead.model_validate(task) for task in tasks],
        harvests=[
            {
                "id": str(item.id),
                "harvested_on": item.harvested_on.isoformat(),
                "quantity_kg": float(item.quantity_kg) if item.quantity_kg is not None else None,
                "used_for": item.used_for.value,
            }
            for item in harvests
        ],
    )


@PLANTINGS.patch("/{planting_id}", response_model=PlantingSummary, summary="Pause or finish")
def update_planting(
    planting_id: uuid.UUID, payload: PlantingUpdate, db: DbSession, user: CurrentUser
) -> PlantingSummary:
    planting = db.get(Planting, planting_id)
    if planting is None or planting.user_id != user.id:
        raise not_found("planting")

    if payload.status is not None:
        if payload.status is PlantingStatus.PAUSED:
            planting.paused_at = datetime.now(UTC)
            for task in db.scalars(
                select(TaskInstance).where(
                    TaskInstance.planting_id == planting.id,
                    TaskInstance.status == TaskInstanceStatus.PENDING,
                )
            ):
                task.status = TaskInstanceStatus.SNOOZED
        elif payload.status is PlantingStatus.FINISHED:
            planting.finished_at = datetime.now(UTC)
            for task in db.scalars(
                select(TaskInstance).where(
                    TaskInstance.planting_id == planting.id,
                    TaskInstance.status.in_(
                        [TaskInstanceStatus.PENDING, TaskInstanceStatus.SNOOZED]
                    ),
                )
            ):
                task.status = TaskInstanceStatus.SKIPPED
                task.skipped_reason = "planting_finished"
        elif payload.status is PlantingStatus.ACTIVE:
            planting.paused_at = None
            planting.finished_at = None
        planting.status = payload.status

    if payload.notes is not None:
        planting.notes = payload.notes

    db.commit()
    db.refresh(planting)
    return _planting_summary(db, planting, db.get(Product, planting.product_id))


# Tasks


def _own_task(db, task_id: uuid.UUID, user) -> TaskInstance:
    task = db.get(TaskInstance, task_id)
    if task is None or task.user_id != user.id:
        raise not_found("task")
    return task


@TASKS.get("/today", response_model=TodayResponse, summary="Today's tasks, morning and evening")
def today(db: DbSession, user: CurrentUser) -> TodayResponse:
    grouped = tasks_due_today(db, user.id)
    completed = (
        db.scalar(
            select(func.count())
            .select_from(TaskInstance)
            .where(
                TaskInstance.user_id == user.id,
                TaskInstance.status == TaskInstanceStatus.DONE,
                TaskInstance.completed_at
                >= datetime.now(KIGALI).replace(hour=0, minute=0, second=0, microsecond=0),
            )
        )
        or 0
    )
    return TodayResponse(
        date=datetime.now(KIGALI).date(),
        morning=[TaskRead.model_validate(task) for task in grouped["morning"]],
        evening=[TaskRead.model_validate(task) for task in grouped["evening"]],
        completed_today=int(completed),
    )


@TASKS.post("/{task_id}/done", response_model=TaskRead, summary="Mark a task done")
def complete_task(task_id: uuid.UUID, db: DbSession, user: CurrentUser) -> TaskRead:
    task = _own_task(db, task_id, user)
    if task.status is TaskInstanceStatus.DONE:
        return TaskRead.model_validate(task)
    task.status = TaskInstanceStatus.DONE
    task.completed_at = datetime.now(UTC)
    task.snoozed_until = None
    db.commit()
    db.refresh(task)
    return TaskRead.model_validate(task)


@TASKS.post("/{task_id}/snooze", response_model=TaskRead, summary="Snooze a task")
def snooze_task(
    task_id: uuid.UUID, payload: SnoozeRequest, db: DbSession, user: CurrentUser
) -> TaskRead:
    task = _own_task(db, task_id, user)
    if task.status is TaskInstanceStatus.DONE:
        raise unprocessable(
            "task.already_done", "errors.task.already_done", "This task is already done"
        )
    task.snoozed_until = datetime.now(UTC) + timedelta(hours=payload.hours)
    task.due_at = task.snoozed_until
    task.status = TaskInstanceStatus.SNOOZED
    task.reminder_sent_at = None
    db.commit()
    db.refresh(task)
    return TaskRead.model_validate(task)


@TASKS.post("/{task_id}/skip", response_model=TaskRead, summary="Skip a task with a reason")
def skip_task(
    task_id: uuid.UUID, payload: SkipRequest, db: DbSession, user: CurrentUser
) -> TaskRead:
    task = _own_task(db, task_id, user)
    task.status = TaskInstanceStatus.SKIPPED
    task.skipped_reason = payload.reason
    db.commit()
    db.refresh(task)
    return TaskRead.model_validate(task)


# Notifications


def _notification_read(db, notification: Notification, language: Language) -> NotificationRead:
    """Attach the resolved title and body so the app shows the edited copy."""
    payload = NotificationRead.model_validate(notification)
    params = notification.params or {}
    title_key = notification.title_translation_key.rsplit(".", 1)[0]
    body_key = notification.body_translation_key.rsplit(".", 1)[0]
    payload.title = templates.title_for(title_key, language.value)
    payload.body = templates.body_for(db, body_key, language.value, params)
    return payload


@NOTIFICATIONS.get("", response_model=Page[NotificationRead], summary="My notifications")
def list_notifications(
    params: PageParamsDep,
    db: DbSession,
    user: CurrentUser,
    filter_by: Annotated[str, Query(pattern="^(all|unread|missed)$")] = "all",
) -> dict:
    query = select(Notification).where(Notification.user_id == user.id)
    if filter_by == "unread":
        query = query.where(Notification.read_at.is_(None))
    elif filter_by == "missed":
        query = query.where(Notification.type == NotificationType.TASK_MISSED)

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(
        query.order_by(Notification.created_at.desc()).offset(params.offset).limit(params.page_size)
    )
    return build_page([_notification_read(db, row, user.language) for row in rows], total, params)


@NOTIFICATIONS.get("/unread-count", summary="Unread badge count")
def unread_count(db: DbSession, user: CurrentUser) -> dict:
    count = (
        db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.user_id == user.id, Notification.read_at.is_(None))
        )
        or 0
    )
    return {"unread": int(count)}


@NOTIFICATIONS.post("/{notification_id}/read", response_model=NotificationRead, summary="Mark read")
def mark_read(notification_id: uuid.UUID, db: DbSession, user: CurrentUser) -> NotificationRead:
    notification = db.get(Notification, notification_id)
    if notification is None or notification.user_id != user.id:
        raise not_found("notification")
    if notification.read_at is None:
        notification.read_at = datetime.now(UTC)
        db.commit()
    return _notification_read(db, notification, user.language)


# Harvests


@HARVESTS.post("", response_model=HarvestRead, status_code=201, summary="Record a harvest")
def record_harvest(payload: HarvestCreate, db: DbSession, user: CurrentUser) -> HarvestRead:
    planting = db.get(Planting, payload.planting_id)
    if planting is None or planting.user_id != user.id:
        raise not_found("planting")

    harvest = Harvest(
        planting_id=planting.id,
        user_id=user.id,
        harvested_on=payload.harvested_on or datetime.now(KIGALI).date(),
        quantity_kg=payload.quantity_kg,
        quantity_note=payload.quantity_note,
        used_for=payload.used_for,
        notes=payload.notes,
    )
    db.add(harvest)
    db.commit()
    db.refresh(harvest)
    return HarvestRead.model_validate(harvest)


@HARVESTS.get("", response_model=Page[HarvestRead], summary="My harvests")
def list_harvests(params: PageParamsDep, db: DbSession, user: CurrentUser) -> dict:
    query = select(Harvest).where(Harvest.user_id == user.id)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(
        query.order_by(Harvest.harvested_on.desc()).offset(params.offset).limit(params.page_size)
    )
    return build_page([HarvestRead.model_validate(row) for row in rows], total, params)


@HARVESTS.get("/summary", response_model=HarvestSummary, summary="Harvest totals")
def harvest_summary(db: DbSession, user: CurrentUser) -> HarvestSummary:
    rows = db.execute(
        select(Harvest.used_for, func.coalesce(func.sum(Harvest.quantity_kg), 0), func.count())
        .where(Harvest.user_id == user.id)
        .group_by(Harvest.used_for)
    ).all()
    by_use = {used.value: float(total) for used, total, _ in rows}
    return HarvestSummary(
        total_kg=round(sum(by_use.values()), 3),
        by_use=by_use,
        count=sum(count for _, _, count in rows),
    )


# Specialist requests


@SPECIALIST.post("", response_model=SpecialistRequestRead, status_code=201, summary="Ask for help")
def create_specialist_request(
    payload: SpecialistRequestCreate, db: DbSession, user: CurrentUser
) -> SpecialistRequestRead:
    request = SpecialistRequest(
        user_id=user.id,
        product_id=payload.product_id,
        specialist_type=payload.specialist_type,
        description=payload.description,
        photo_url=payload.photo_url,
        status=SpecialistRequestStatus.NEW,
    )
    db.add(request)
    db.commit()
    db.refresh(request)
    broadcaster.notify_admins(
        db,
        "specialist_request.created",
        {"request_id": str(request.id), "type": request.specialist_type.value},
    )
    return SpecialistRequestRead.model_validate(request)


@SPECIALIST.get("", response_model=Page[SpecialistRequestRead], summary="My help requests")
def list_specialist_requests(params: PageParamsDep, db: DbSession, user: CurrentUser) -> dict:
    query = select(SpecialistRequest).where(SpecialistRequest.user_id == user.id)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(
        query.order_by(SpecialistRequest.created_at.desc())
        .offset(params.offset)
        .limit(params.page_size)
    )
    return build_page([SpecialistRequestRead.model_validate(row) for row in rows], total, params)


# Funding interest


@FUNDING.post(
    "", response_model=FundingInterestRead, status_code=201, summary="Grow bigger request"
)
def create_funding_interest(
    payload: FundingInterestCreate, db: DbSession, user: CurrentUser
) -> FundingInterestRead:
    record = FundingInterest(
        user_id=user.id,
        product_id=payload.product_id,
        planned_size=payload.planned_size,
        location=payload.location,
        building_or_land=payload.building_or_land,
        experience=payload.experience,
        funding_needed=payload.funding_needed,
        consent_to_share=payload.consent_to_share,
        consent_at=datetime.now(UTC),
        status=FundingStatus.NEW,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    broadcaster.notify_admins(
        db, "funding_interest.created", {"funding_interest_id": str(record.id)}
    )
    return FundingInterestRead.model_validate(record)


@FUNDING.get("", response_model=Page[FundingInterestRead], summary="My funding interest")
def list_funding_interest(params: PageParamsDep, db: DbSession, user: CurrentUser) -> dict:
    query = select(FundingInterest).where(FundingInterest.user_id == user.id)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(
        query.order_by(FundingInterest.created_at.desc())
        .offset(params.offset)
        .limit(params.page_size)
    )
    return build_page([FundingInterestRead.model_validate(row) for row in rows], total, params)


@router.get("/partners", summary="Validation and funding partners shown in the app")
def partners() -> dict:
    return {
        "partners": [
            {
                "code": "RAB",
                "name": "Rwanda Agriculture and Animal Resources Development Board",
                "role": "outreach_and_validation",
            },
            {"code": "BRD", "name": "Development Bank of Rwanda", "role": "funding"},
        ]
    }


for sub_router in (PRODUCTS, PLANTINGS, TASKS, NOTIFICATIONS, HARVESTS, SPECIALIST, FUNDING):
    router.include_router(sub_router)
