"""Turning a product schedule into concrete tasks, in Africa/Kigali."""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models.enums import RepeatRule, TaskType, TimeOfDay
from app.models.farming import Planting, TaskInstance
from app.models.product import Product, ProductScheduleTask
from app.models.user import User

logger = logging.getLogger("agura.scheduling")

KIGALI = ZoneInfo("Africa/Kigali")
MAX_TASKS_PER_PLANTING = 400
# A planting that never ends would generate tasks forever.
DEFAULT_HORIZON_DAYS = 180


def morning_time(user: User | None) -> time:
    return user.reminder_morning_time if user else time(7, 0)


def evening_time(user: User | None) -> time:
    return user.reminder_evening_time if user else time(18, 0)


def due_datetime(
    started_on: date, offset_days: int, time_of_day: TimeOfDay, user: User | None
) -> datetime:
    """Local Kigali wall clock time for a task, stored as an aware datetime."""
    day = started_on + timedelta(days=offset_days)
    clock = morning_time(user) if time_of_day is TimeOfDay.MORNING else evening_time(user)
    return datetime.combine(day, clock, tzinfo=KIGALI)


def expand_schedule(
    task: ProductScheduleTask, started_on: date, horizon_days: int = DEFAULT_HORIZON_DAYS
) -> list[tuple[int, TimeOfDay]]:
    """Day offsets and times of day for one schedule task.

    A weekly task belongs to its chosen weekday, so it does not also fire on the
    start day unless the start day already is that weekday.
    """
    rule = task.repeat_rule
    base_offset = int(task.day_offset or 0)

    if rule is RepeatRule.ONCE:
        return [(base_offset, task.time_of_day)]

    until = task.repeat_until_day or horizon_days
    until = min(until, horizon_days)

    occurrences: list[tuple[int, TimeOfDay]] = []

    if rule is RepeatRule.WEEKLY:
        # Weekly means the same weekday every week, morning or evening as configured.
        target_weekday = task.repeat_weekday if task.repeat_weekday is not None else 0
        day = started_on + timedelta(days=base_offset)
        offset = base_offset + (target_weekday - day.weekday()) % 7
        while offset <= until:
            occurrences.append((offset, task.time_of_day))
            offset += 7
        return occurrences

    # Daily starts on the start day itself.
    occurrences.append((base_offset, task.time_of_day))
    if rule is RepeatRule.DAILY:
        for offset in range(base_offset + 1, until + 1):
            occurrences.append((offset, task.time_of_day))
        return occurrences

    if rule is RepeatRule.CUSTOM:
        interval = task.repeat_interval_days or 7
        offset = base_offset + interval
        while offset <= until:
            occurrences.append((offset, task.time_of_day))
            offset += interval
        return occurrences

    return occurrences


def build_task_instances(
    db: Session,
    planting: Planting,
    product: Product,
    user: User | None,
    *,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> list[TaskInstance]:
    """Create every pending task for a new planting."""
    created: list[TaskInstance] = []
    for schedule_task in sorted(product.schedule_tasks, key=lambda item: item.day_offset):
        for offset, time_of_day in expand_schedule(
            schedule_task, planting.started_on, horizon_days
        ):
            if len(created) >= MAX_TASKS_PER_PLANTING:
                logger.warning(
                    "Planting %s hit the task limit, later occurrences were skipped",
                    planting.id,
                )
                return created
            created.append(
                TaskInstance(
                    planting_id=planting.id,
                    user_id=planting.user_id,
                    product_schedule_task_id=schedule_task.id,
                    task_type=schedule_task.task_type,
                    due_at=due_datetime(planting.started_on, offset, time_of_day, user),
                    time_of_day=time_of_day,
                    instruction_text=schedule_task.instruction_text,
                    is_urgent=schedule_task.task_type is TaskType.SPRAY_OR_TREATMENT,
                )
            )
    db.add_all(created)
    db.flush()
    return created


def expected_harvest_date(product: Product, started_on: date) -> date | None:
    days = product.time_to_harvest_max_days or product.time_to_harvest_min_days
    if days is None:
        return None
    return started_on + timedelta(days=int(days))


def first_tasks(db: Session, planting_id, limit: int = 3) -> list[TaskInstance]:
    from sqlalchemy import select

    return list(
        db.scalars(
            select(TaskInstance)
            .where(TaskInstance.planting_id == planting_id)
            .order_by(TaskInstance.due_at)
            .limit(limit)
        )
    )


def resync_planting_after_product_change(
    db: Session, planting_id, product: Product, user: User | None
) -> int:
    """Apply a new product version to one planting, keeping completed history."""
    from sqlalchemy import delete

    planting = db.get(Planting, planting_id)
    if planting is None:
        return 0
    db.execute(
        delete(TaskInstance).where(
            TaskInstance.planting_id == planting.id,
            TaskInstance.status.in_(["pending", "snoozed"]),
        )
    )
    planting.product_version = product.version
    db.flush()
    return len(build_task_instances(db, planting, product, user))
