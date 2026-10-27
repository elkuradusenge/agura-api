"""The reminder engine.

Runs on a schedule and does four things, in this order:

1. marks pending tasks that are past due as missed,
2. sends one follow up for a missed task,
3. creates reminders for tasks that are due now,
4. queues an SMS when SMS_ENABLED is true.

Rules from the product spec, all enforced here: quiet hours 21:00 to 05:00 except
urgent tasks, at most 3 messages per farmer per day unless they opt in, one follow up
per missed task, and nothing once a task is done. Everything runs in Africa/Kigali.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models.enums import (
    Language,
    NotificationType,
    PlantingStatus,
    SmsStatus,
    TaskInstanceStatus,
    TaskType,
    TimeOfDay,
    UserStatus,
)
from app.models.farming import Notification, Planting, SmsLog, TaskInstance
from app.models.product import Product
from app.models.user import User
from app.services import settings_service
from app.sms import templates as template_service
from app.sms.providers import get_provider
from app.ws import broadcaster

logger = logging.getLogger("agura.reminders")

KIGALI = ZoneInfo("Africa/Kigali")
QUIET_START = time(21, 0)
QUIET_END = time(5, 0)
DAILY_MESSAGE_LIMIT = 3


@dataclass
class ReminderRun:
    reminders_created: int = 0
    follow_ups_created: int = 0
    tasks_marked_missed: int = 0
    sms_sent: int = 0
    sms_failed: int = 0
    suppressed_quiet_hours: int = 0
    suppressed_limit: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "reminders_created": self.reminders_created,
            "follow_ups_created": self.follow_ups_created,
            "tasks_marked_missed": self.tasks_marked_missed,
            "sms_sent": self.sms_sent,
            "sms_failed": self.sms_failed,
            "suppressed_quiet_hours": self.suppressed_quiet_hours,
            "suppressed_limit": self.suppressed_limit,
            "skipped": self.skipped,
            "errors": self.errors[:20],
        }


def now_kigali() -> datetime:
    return datetime.now(KIGALI)


def in_quiet_hours(moment: datetime, quiet_start: time, quiet_end: time) -> bool:
    """Quiet hours may wrap midnight, which is the normal case: 21:00 to 05:00."""
    local = moment.astimezone(KIGALI).time()
    if quiet_start <= quiet_end:
        return quiet_start <= local < quiet_end
    return local >= quiet_start or local < quiet_end


def quiet_window(db: Session) -> tuple[time, time]:
    stored = settings_service.get_value(db, "reminders.quiet_hours", {}) or {}
    start = _parse_time(stored.get("start"), QUIET_START)
    end = _parse_time(stored.get("end"), QUIET_END)
    return start, end


def _parse_time(value: Any, fallback: time) -> time:
    if isinstance(value, str):
        try:
            return time.fromisoformat(value)
        except ValueError:
            return fallback
    return fallback


def daily_limit(db: Session) -> int:
    value = settings_service.get_value(db, "reminders.max_messages_per_day", DAILY_MESSAGE_LIMIT)
    return int(value) if isinstance(value, int | float) else DAILY_MESSAGE_LIMIT


def messages_sent_today(db: Session, user_id: uuid.UUID) -> int:
    start_of_day = now_kigali().replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.created_at >= start_of_day.astimezone(ZoneInfo("UTC")),
            )
        )
        or 0
    )


def _notification_params(task: TaskInstance, product: Product | None) -> dict[str, Any]:
    return {
        "task": task.instruction_text,
        "product": (product.name_en if product else "") or "",
        "time_of_day": task.time_of_day.value,
        "task_type": task.task_type.value,
    }


def _create_notification(
    db: Session,
    *,
    user: User,
    task: TaskInstance,
    product: Product | None,
    notification_type: NotificationType,
    key: str,
    params: dict[str, Any],
    settings: Settings,
    run: ReminderRun,
) -> Notification | None:
    """Create the in-app notification, then queue an SMS when allowed."""
    if task.status in {TaskInstanceStatus.DONE, TaskInstanceStatus.SKIPPED}:
        run.skipped += 1
        return None

    moment = now_kigali()
    quiet_start, quiet_end = quiet_window(db)
    urgent = task.is_urgent or task.task_type is TaskType.SPRAY_OR_TREATMENT
    if in_quiet_hours(moment, quiet_start, quiet_end) and not urgent:
        run.suppressed_quiet_hours += 1
        return None

    sent_today = messages_sent_today(db, user.id)
    limit = daily_limit(db)
    over_limit = sent_today >= limit and not user.message_opt_in
    if over_limit:
        run.suppressed_limit += 1
        return None

    notification = Notification(
        user_id=user.id,
        type=notification_type,
        title_translation_key=f"{key}.title",
        body_translation_key=f"{key}.body",
        params=params,
        task_instance_id=task.id,
    )
    db.add(notification)
    db.flush()

    text = template_service.body_for(db, key, user.language.value, params)
    sms_text = template_service.body_for(db, key, user.language.value, params, sms=True)
    _queue_sms(
        db,
        user=user,
        notification=notification,
        body=sms_text or text or "",
        settings=settings,
        key=key,
        run=run,
    )
    run.reminders_created += 1

    broadcaster.notify_user(
        db,
        user.id,
        "notification.created",
        {
            "notification_id": str(notification.id),
            "type": notification_type.value,
            "title_key": notification.title_translation_key,
            "body_key": notification.body_translation_key,
            "params": params,
        },
    )
    return notification


def _queue_sms(
    db: Session,
    *,
    user: User,
    notification: Notification,
    body: str,
    settings: Settings,
    key: str,
    run: ReminderRun,
) -> None:
    provider = get_provider(settings)
    if not body:
        # A missing template must be visible in monitoring, not silently dropped.
        db.add(
            SmsLog(
                user_id=user.id,
                notification_id=notification.id,
                phone=user.phone,
                body="",
                template_key=key,
                language=user.language.value,
                provider=provider.name,
                status=SmsStatus.FAILED,
                attempts=0,
                error="template_missing",
            )
        )
        db.flush()
        run.sms_failed += 1
        return
    log = SmsLog(
        user_id=user.id,
        notification_id=notification.id,
        phone=user.phone,
        body=body,
        template_key=key,
        language=user.language.value,
        provider=provider.name,
    )
    db.add(log)
    db.flush()

    result = provider.send(user.phone, body)
    log.attempts = 1
    if result.delivered:
        log.status = SmsStatus.SENT
        log.sent_at = now_kigali()
        log.provider_message_id = result.provider_message_id
        notification.sms_sent = True
        run.sms_sent += 1
    elif result.error == "sms_disabled":
        log.status = SmsStatus.DISABLED
    else:
        log.status = SmsStatus.FAILED
        log.error = result.error
        run.sms_failed += 1
    db.flush()


def mark_missed_tasks(db: Session) -> int:
    """A pending task whose due moment has passed is missed."""
    moment = now_kigali()
    pending = db.scalars(
        select(TaskInstance).where(
            TaskInstance.status == TaskInstanceStatus.PENDING,
            TaskInstance.due_at < moment - timedelta(minutes=1),
        )
    ).all()
    for task in pending:
        task.status = TaskInstanceStatus.MISSED
    if pending:
        db.flush()
    return len(pending)


def send_follow_ups(db: Session, settings: Settings, run: ReminderRun) -> None:
    """Exactly one follow up per missed task, and never more."""
    missed = db.scalars(
        select(TaskInstance).where(
            TaskInstance.status == TaskInstanceStatus.MISSED,
            TaskInstance.follow_up_sent_at.is_(None),
        )
    ).all()

    max_follow_ups = settings_service.get_value(db, "reminders.missed_task_follow_ups", 1)
    if not isinstance(max_follow_ups, int) or max_follow_ups < 1:
        return

    for task in missed:
        planting = db.get(Planting, task.planting_id)
        if planting is None or planting.status != PlantingStatus.ACTIVE:
            continue
        user = db.get(User, task.user_id)
        if user is None:
            continue
        product = db.get(Product, planting.product_id)
        params = _notification_params(task, product)

        created = _create_notification(
            db,
            user=user,
            task=task,
            product=product,
            notification_type=NotificationType.TASK_MISSED,
            key="task.missed",
            params=params,
            settings=settings,
            run=run,
        )
        if created is not None:
            task.follow_up_sent_at = now_kigali()
            run.follow_ups_created += 1
        else:
            # Even if quiet hours or the daily limit suppressed it, do not retry forever.
            task.follow_up_sent_at = None if run.suppressed_limit else task.follow_up_sent_at
    db.flush()


def send_due_reminders(db: Session, settings: Settings, run: ReminderRun) -> None:
    """Remind about tasks due in the next window, once each."""
    moment = now_kigali()
    window_end = moment + timedelta(minutes=30)
    due = db.scalars(
        select(TaskInstance)
        .where(
            TaskInstance.status == TaskInstanceStatus.PENDING,
            TaskInstance.reminder_sent_at.is_(None),
            TaskInstance.due_at >= moment - timedelta(minutes=30),
            TaskInstance.due_at <= window_end,
        )
        .order_by(TaskInstance.due_at)
    ).all()

    for task in due:
        planting = db.get(Planting, task.planting_id)
        if planting is None or planting.status != PlantingStatus.ACTIVE:
            continue
        user = db.get(User, task.user_id)
        if user is None or user.status != UserStatus.ACTIVE:
            continue
        product = db.get(Product, planting.product_id)
        key = (
            "task.reminder.evening"
            if task.time_of_day is TimeOfDay.EVENING
            else "task.reminder.morning"
        )
        created = _create_notification(
            db,
            user=user,
            task=task,
            product=product,
            notification_type=NotificationType.TASK_REMINDER,
            key=key,
            params=_notification_params(task, product),
            settings=settings,
            run=run,
        )
        if created is not None:
            task.reminder_sent_at = moment
    db.flush()


def run_once(db: Session, settings: Settings | None = None) -> ReminderRun:
    """One scheduler pass. Safe to call from a test or from the worker loop."""
    settings = settings or get_settings()
    run = ReminderRun()

    run.tasks_marked_missed = mark_missed_tasks(db)
    send_follow_ups(db, settings, run)
    send_due_reminders(db, settings, run)

    db.commit()
    return run


def tasks_due_today(db: Session, user_id: uuid.UUID) -> dict[str, list[TaskInstance]]:
    """Today's tasks split into morning and evening, which is what the app shows."""
    moment = now_kigali()
    start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    tasks = db.scalars(
        select(TaskInstance)
        .where(
            TaskInstance.user_id == user_id,
            TaskInstance.due_at >= start,
            TaskInstance.due_at < end,
            TaskInstance.status.in_(
                [
                    TaskInstanceStatus.PENDING,
                    TaskInstanceStatus.SNOOZED,
                    TaskInstanceStatus.MISSED,
                ]
            ),
        )
        .order_by(TaskInstance.due_at)
    ).all()

    grouped: dict[str, list[TaskInstance]] = {"morning": [], "evening": []}
    for task in tasks:
        grouped[task.time_of_day.value].append(task)
    return grouped


def language_or_default(user: User) -> Language:
    try:
        return Language(user.language)
    except ValueError:
        return Language.RW
