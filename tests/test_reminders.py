"""Reminder scheduling: repeats, quiet hours, daily limits and follow ups."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy import text

from app.config import get_settings
from app.models.enums import (
    Language,
    PlantingStatus,
    ProductStatus,
    RepeatRule,
    SmsStatus,
    TaskInstanceStatus,
    TaskType,
    TimeOfDay,
)
from app.models.farming import Planting, TaskInstance
from app.models.product import ProductScheduleTask
from app.scheduler import reminders
from app.scheduler.tasks import KIGALI, due_datetime, expand_schedule
from app.services import settings_service
from tests.conftest import API, user_headers

MONDAY = date(2026, 3, 2)


def make_task(**overrides) -> ProductScheduleTask:
    defaults = {
        "task_type": TaskType.IRRIGATION,
        "day_offset": 0,
        "repeat_rule": RepeatRule.ONCE,
        "time_of_day": TimeOfDay.MORNING,
        "instruction_text": "Mist the bags.",
    }
    defaults.update(overrides)
    return ProductScheduleTask(**defaults)


class TestScheduleExpansion:
    def test_once_produces_a_single_occurrence(self):
        assert expand_schedule(make_task(), MONDAY) == [(0, TimeOfDay.MORNING)]

    def test_daily_repeats_up_to_the_until_day(self):
        task = make_task(repeat_rule=RepeatRule.DAILY, repeat_until_day=3)
        offsets = [offset for offset, _ in expand_schedule(task, MONDAY)]
        assert offsets == [0, 1, 2, 3]

    def test_daily_respects_the_horizon(self):
        task = make_task(repeat_rule=RepeatRule.DAILY, repeat_until_day=1000)
        offsets = [offset for offset, _ in expand_schedule(task, MONDAY, horizon_days=10)]
        assert offsets[0] == 0
        assert max(offsets) <= 10

    def test_weekly_lands_on_the_requested_weekday(self):
        # 2026-03-02 is a Monday. Ask for Friday (weekday 4).
        task = make_task(repeat_rule=RepeatRule.WEEKLY, repeat_weekday=4, repeat_until_day=21)
        occurrences = expand_schedule(task, MONDAY)
        offsets = [offset for offset, _ in occurrences]
        for offset in offsets[1:]:
            assert (MONDAY + timedelta(days=offset)).weekday() == 4
        assert offsets == [4, 11, 18]

    def test_weekly_keeps_morning_and_evening_separate(self):
        morning = make_task(
            repeat_rule=RepeatRule.WEEKLY,
            repeat_weekday=0,
            repeat_until_day=14,
            time_of_day=TimeOfDay.MORNING,
        )
        evening = make_task(
            repeat_rule=RepeatRule.WEEKLY,
            repeat_weekday=0,
            repeat_until_day=14,
            time_of_day=TimeOfDay.EVENING,
        )
        assert all(
            time_of_day is TimeOfDay.MORNING for _, time_of_day in expand_schedule(morning, MONDAY)
        )
        assert all(
            time_of_day is TimeOfDay.EVENING for _, time_of_day in expand_schedule(evening, MONDAY)
        )

    def test_custom_interval(self):
        task = make_task(repeat_rule=RepeatRule.CUSTOM, repeat_interval_days=3, repeat_until_day=10)
        offsets = [offset for offset, _ in expand_schedule(task, MONDAY)]
        assert offsets == [0, 3, 6, 9]


class TestQuietHours:
    def test_window_that_wraps_midnight(self):
        quiet_start, quiet_end = time(21, 0), time(5, 0)
        at_2300 = datetime(2026, 3, 2, 23, 0, tzinfo=KIGALI)
        at_0400 = datetime(2026, 3, 3, 4, 0, tzinfo=KIGALI)
        at_1200 = datetime(2026, 3, 2, 12, 0, tzinfo=KIGALI)
        assert reminders.in_quiet_hours(at_2300, quiet_start, quiet_end) is True
        assert reminders.in_quiet_hours(at_0400, quiet_start, quiet_end) is True
        assert reminders.in_quiet_hours(at_1200, quiet_start, quiet_end) is False

    def test_window_within_the_same_day(self):
        assert reminders.in_quiet_hours(
            datetime(2026, 3, 2, 14, 0, tzinfo=KIGALI), time(13, 0), time(15, 0)
        )
        assert not reminders.in_quiet_hours(
            datetime(2026, 3, 2, 16, 0, tzinfo=KIGALI), time(13, 0), time(15, 0)
        )


@pytest.fixture
def farmer_context(db, user_factory, seeded_food_items):
    """A farmer with a live product and a planting, ready for the reminder engine."""
    from scripts.seed_product import seed_mushroom_product

    user = user_factory(phone="+250788500001")
    from app.sms.templates import seed_templates

    seed_templates(db)
    product = seed_mushroom_product(db)
    # Force the seeded draft live for scheduling tests only.
    product.status = ProductStatus.TRACKABLE
    product.reviewer_name = "Dr Uwase"
    product.reviewer_organisation = "RAB"
    product.review_date = date(2026, 1, 15)
    db.commit()

    planting = Planting(
        user_id=user.id,
        product_id=product.id,
        product_version=product.version,
        status=PlantingStatus.ACTIVE,
        quantity=1,
        started_on=date.today(),
    )
    db.add(planting)
    db.commit()
    return user, product, planting


def add_task(
    db,
    planting,
    *,
    due_at,
    time_of_day=TimeOfDay.MORNING,
    task_type=TaskType.CHECK,
    urgent=False,
    status=TaskInstanceStatus.PENDING,
):
    task = TaskInstance(
        planting_id=planting.id,
        user_id=planting.user_id,
        task_type=task_type,
        due_at=due_at,
        time_of_day=time_of_day,
        instruction_text="Check the bags.",
        status=status,
        is_urgent=urgent,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def safe_quiet_hours() -> dict[str, str]:
    """A quiet window that certainly does not contain the current hour."""
    hour = datetime.now(KIGALI).hour
    if hour >= 12:
        return {"start": "00:00", "end": "00:01"}
    return {"start": "23:00", "end": "23:30"}


def due_soon(minutes: int = 5) -> datetime:
    """A due time inside the reminder window and in the future."""
    return datetime.now(KIGALI) + timedelta(minutes=minutes)


def test_due_task_creates_a_notification(db, farmer_context):
    user, _product, planting = farmer_context
    settings_service.seed_defaults(db)
    add_task(db, planting, due_at=due_soon())

    result = reminders.run_once(db, get_settings())
    assert result.reminders_created >= 1
    assert result.tasks_marked_missed == 0

    notification = db.execute(
        text("SELECT title_translation_key FROM notifications WHERE user_id = :uid LIMIT 1"),
        {"uid": str(user.id)},
    ).one_or_none()
    db.rollback()
    assert notification is not None
    assert notification.title_translation_key == "task.reminder.morning.title"


def test_evening_task_uses_the_evening_template(db, farmer_context):
    user, _product, planting = farmer_context
    settings_service.seed_defaults(db)
    add_task(db, planting, due_at=due_soon(), time_of_day=TimeOfDay.EVENING)

    reminders.run_once(db, get_settings())
    keys = (
        db.execute(
            text("SELECT title_translation_key FROM notifications WHERE user_id = :uid"),
            {"uid": str(user.id)},
        )
        .scalars()
        .all()
    )
    db.rollback()
    assert "task.reminder.evening.title" in keys


def test_quiet_hours_suppress_non_urgent_reminders(db, farmer_context):
    user, _product, planting = farmer_context
    settings_service.set_value(db, "reminders.quiet_hours", {"start": "00:00", "end": "23:59"})
    # A due task inside the next 30 minutes, which is inside the quiet window.
    add_task(db, planting, due_at=due_soon())

    result = reminders.run_once(db, get_settings())
    assert result.reminders_created == 0
    assert result.suppressed_quiet_hours >= 1

    count = db.scalar(
        text("SELECT count(*) FROM notifications WHERE user_id = :uid"), {"uid": str(user.id)}
    )
    db.rollback()
    assert count == 0


def test_urgent_tasks_bypass_quiet_hours(db, farmer_context):
    _user, _product, planting = farmer_context
    settings_service.set_value(db, "reminders.quiet_hours", {"start": "00:00", "end": "23:59"})
    add_task(
        db,
        planting,
        due_at=due_soon(),
        task_type=TaskType.SPRAY_OR_TREATMENT,
        urgent=True,
    )

    result = reminders.run_once(db, get_settings())
    assert result.reminders_created == 1


def test_daily_limit_stops_further_messages(db, farmer_context):
    user, _product, planting = farmer_context
    settings_service.set_value(db, "reminders.quiet_hours", safe_quiet_hours())
    settings_service.set_value(db, "reminders.max_messages_per_day", 2)

    for index in range(5):
        add_task(db, planting, due_at=due_soon(minutes=index + 1))

    result = reminders.run_once(db, get_settings())
    assert result.reminders_created == 2
    assert result.suppressed_limit >= 3


def test_farmer_opt_in_removes_the_daily_limit(db, farmer_context):
    user, _product, planting = farmer_context
    user.message_opt_in = True
    db.commit()
    settings_service.set_value(db, "reminders.quiet_hours", safe_quiet_hours())
    settings_service.set_value(db, "reminders.max_messages_per_day", 1)

    for index in range(3):
        add_task(db, planting, due_at=due_soon(minutes=index + 1))

    result = reminders.run_once(db, get_settings())
    assert result.reminders_created == 3


def test_completed_tasks_are_never_reminded(db, farmer_context):
    _user, _product, planting = farmer_context
    add_task(db, planting, due_at=due_soon(), status=TaskInstanceStatus.DONE)
    result = reminders.run_once(db, get_settings())
    assert result.reminders_created == 0


def test_missed_task_gets_exactly_one_follow_up(db, farmer_context):
    _user, _product, planting = farmer_context
    settings_service.set_value(db, "reminders.quiet_hours", safe_quiet_hours())
    task = add_task(db, planting, due_at=datetime.now(KIGALI) - timedelta(days=1))

    first = reminders.run_once(db, get_settings())
    assert first.tasks_marked_missed == 1
    assert first.follow_ups_created == 1

    second = reminders.run_once(db, get_settings())
    assert second.follow_ups_created == 0

    total = db.scalar(
        text("SELECT count(*) FROM notifications " "WHERE user_id = :uid AND type = 'task_missed'"),
        {"uid": str(task.user_id)},
    )
    db.rollback()
    assert total == 1


def test_sms_is_not_sent_when_disabled(db, farmer_context):
    _user, _product, planting = farmer_context
    settings = get_settings()
    settings.sms_enabled = False
    settings_service.seed_defaults(db)
    add_task(db, planting, due_at=due_soon())

    try:
        reminders.run_once(db, settings)
    finally:
        settings.sms_enabled = False

    statuses = db.execute(text("SELECT status FROM sms_log")).scalars().all()
    db.rollback()
    assert statuses
    assert all(status == SmsStatus.DISABLED.value for status in statuses)


def test_sms_is_queued_with_the_console_provider_when_enabled(db, farmer_context):
    _user, _product, planting = farmer_context
    settings = get_settings()
    settings.sms_enabled = True
    settings.sms_provider = "console"
    settings_service.seed_defaults(db)
    add_task(db, planting, due_at=due_soon())

    try:
        result = reminders.run_once(db, settings)
    finally:
        settings.sms_enabled = False

    assert result.sms_sent >= 1
    statuses = db.execute(text("SELECT status, provider FROM sms_log")).all()
    db.rollback()
    assert any(row.status == SmsStatus.SENT.value for row in statuses)
    assert all(row.provider == "console" for row in statuses)


def test_paused_planting_gets_no_reminders(db, farmer_context):
    _user, _product, planting = farmer_context
    planting.status = PlantingStatus.PAUSED
    db.commit()
    settings_service.set_value(db, "reminders.quiet_hours", safe_quiet_hours())
    add_task(db, planting, due_at=due_soon())

    result = reminders.run_once(db, get_settings())
    assert result.reminders_created == 0


def test_due_datetime_uses_kigali_morning_and_evening(db, user_factory):
    user = user_factory(phone="+250788500009")
    user.reminder_morning_time = time(6, 30)
    user.reminder_evening_time = time(19, 0)
    db.commit()

    morning = due_datetime(MONDAY, 0, TimeOfDay.MORNING, user)
    evening = due_datetime(MONDAY, 0, TimeOfDay.EVENING, user)
    assert morning.tzinfo is not None
    assert morning.astimezone(KIGALI).hour == 6
    assert evening.astimezone(KIGALI).hour == 19


def test_todays_tasks_are_grouped_by_time_of_day(client, db, farmer_context):
    user, _product, planting = farmer_context
    today = datetime.now(KIGALI)
    add_task(db, planting, due_at=today.replace(hour=8), time_of_day=TimeOfDay.MORNING)
    add_task(db, planting, due_at=today.replace(hour=18), time_of_day=TimeOfDay.EVENING)

    response = client.get(f"{API}/tasks/today", headers=user_headers(user))
    assert response.status_code == 200
    body = response.json()
    assert len(body["morning"]) == 1
    assert len(body["evening"]) == 1


def test_task_can_be_completed_snoozed_and_skipped(client, db, farmer_context):
    user, _product, planting = farmer_context
    due = datetime.now(KIGALI)
    first = add_task(db, planting, due_at=due)
    second = add_task(db, planting, due_at=due)
    third = add_task(db, planting, due_at=due)
    headers = user_headers(user)

    done = client.post(f"{API}/tasks/{first.id}/done", headers=headers)
    assert done.status_code == 200
    assert done.json()["status"] == "done"
    assert done.json()["completed_at"] is not None

    snoozed = client.post(f"{API}/tasks/{second.id}/snooze", json={"hours": 3}, headers=headers)
    assert snoozed.status_code == 200
    assert snoozed.json()["status"] == "snoozed"

    skipped = client.post(
        f"{API}/tasks/{third.id}/skip", json={"reason": "no materials"}, headers=headers
    )
    assert skipped.status_code == 200
    assert skipped.json()["status"] == "skipped"
    assert skipped.json()["skipped_reason"] == "no materials"


def test_one_farmer_cannot_touch_another_farmers_task(client, db, farmer_context, user_factory):
    _user, _product, planting = farmer_context
    other = user_factory(phone="+250788500002")
    task = add_task(db, planting, due_at=datetime.now(KIGALI))

    response = client.post(f"{API}/tasks/{task.id}/done", headers=user_headers(other))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "task.not_found"


def test_notification_is_delivered_with_rendered_text(db, client, farmer_context):
    """The app renders the admin editable template, not a hardcoded key."""
    user, _product, planting = farmer_context
    settings_service.seed_defaults(db)
    add_task(db, planting, due_at=due_soon())

    reminders.run_once(db, get_settings())

    response = client.get(f"{API}/notifications", headers=user_headers(user))
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["title_translation_key"] == "task.reminder.morning.title"
    # The seeded farmer prefers Kinyarwanda.
    assert item["title"] == "Umurimo w'igitondo"
    assert item["body"] is not None
    assert "Check the bags." in item["body"]


def test_resolved_text_follows_the_farmer_language(db, client, farmer_context):
    user, _product, planting = farmer_context
    user.language = Language.EN
    db.commit()
    settings_service.seed_defaults(db)
    add_task(db, planting, due_at=due_soon())
    reminders.run_once(db, get_settings())

    response = client.get(f"{API}/notifications", headers=user_headers(user))
    item = response.json()["items"][0]
    assert item["title"] == "Morning task"
    assert "Good morning" in item["body"]


def test_edited_template_text_reaches_the_app(db, client, farmer_context):
    from app.sms import templates

    user, _product, planting = farmer_context
    user.language = Language.EN
    db.commit()
    settings_service.seed_defaults(db)
    add_task(db, planting, due_at=due_soon())
    reminders.run_once(db, get_settings())

    template = templates.get_template(db, "task.reminder.morning", "en")
    template.body = "Edited copy for {product}."
    db.commit()

    response = client.get(f"{API}/notifications", headers=user_headers(user))
    assert response.json()["items"][0]["body"] == f"Edited copy for {_product.name_en}."
