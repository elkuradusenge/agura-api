"""Products: the trackable rule, versions and farmer visibility."""

from __future__ import annotations

from sqlalchemy import text

from app.models.enums import (
    AgeGroup,
    ProductStatus,
    TaskType,
)
from app.services import product_service
from scripts.seed_product import seed_mushroom_product
from tests.conftest import API, admin_headers

COMPLETE_PRODUCT = {
    "name_en": "Test product",
    "name_rw": "Igicuruzwa",
    "photo_url": "https://example.test/photo.jpg",
    "description_en": "A complete product used by the tests.",
    "how_not_to_use": "Do not eat raw.",
    "how_to_preserve": "Keep cool.",
    "conditions_diseases": "Educational text about conditions.",
    "reviewer_name": "Dr Uwase",
    "reviewer_organisation": "RAB",
    "review_date": "2026-01-15",
    "time_to_harvest_min_days": 20,
    "time_to_harvest_max_days": 30,
    "harvest_method": "Cut at the base.",
    "irrigation_method": "Mist daily.",
    "irrigation_frequency": "daily",
    "irrigation_time_of_day": "morning",
    "place_type": "shaded_room",
    "recipes": [
        {"name": "Soup", "ingredients": ["mushroom"], "steps": "Simmer."},
        {"name": "Stew", "ingredients": ["mushroom"], "steps": "Cook."},
    ],
    "benefits": [
        {"age_group": group.value, "benefit_text": f"Benefit text for {group.value}"}
        for group in AgeGroup
    ],
    "nutrients": [{"nutrient": "protein", "amount_per_100g": 3.1, "unit": "g"}],
    "schedule_tasks": [
        {
            "task_type": "irrigation",
            "day_offset": 0,
            "repeat_rule": "daily",
            "time_of_day": "morning",
            "instruction_text": "Mist the bags.",
        }
    ],
}


def create_product(client, admin, payload=None):
    return client.post(
        f"{API}/admin/products",
        json=payload or COMPLETE_PRODUCT,
        headers=admin_headers(admin),
    )


def test_new_product_starts_as_a_draft(client, admin_factory):
    admin = admin_factory()
    response = create_product(client, admin)
    assert response.status_code == 201
    assert response.json()["status"] == "draft"
    assert response.json()["version"] == 1


def test_incomplete_product_cannot_become_trackable(client, admin_factory):
    admin = admin_factory()
    product_id = create_product(client, admin, {"name_en": "Bare product"}).json()["id"]

    response = client.post(
        f"{API}/admin/products/{product_id}/mark-trackable", headers=admin_headers(admin)
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "product.not_ready"
    blockers = {item["field"] for item in error["details"]["blockers"]}
    assert "name_rw" in blockers
    assert "reviewer" in blockers
    assert "schedule_tasks" in blockers


def test_complete_product_becomes_trackable_and_is_published(client, admin_factory, db):
    admin = admin_factory()
    product_id = create_product(client, admin).json()["id"]

    response = client.post(
        f"{API}/admin/products/{product_id}/mark-trackable", headers=admin_headers(admin)
    )
    assert response.status_code == 200
    assert response.json()["product"]["status"] == "trackable"

    events = (
        db.execute(text("SELECT type FROM ws_events WHERE type = 'product.published'"))
        .scalars()
        .all()
    )
    assert events


def test_product_without_two_recipes_is_blocked(client, admin_factory):
    admin = admin_factory()
    payload = dict(COMPLETE_PRODUCT)
    payload["recipes"] = [{"name": "Only one", "ingredients": ["x"]}]
    product_id = create_product(client, admin, payload).json()["id"]

    response = client.post(
        f"{API}/admin/products/{product_id}/mark-trackable", headers=admin_headers(admin)
    )
    assert response.status_code == 422
    blockers = {item["field"] for item in response.json()["error"]["details"]["blockers"]}
    assert "recipes" in blockers


def test_missing_review_date_blocks_publication(client, admin_factory):
    admin = admin_factory()
    payload = dict(COMPLETE_PRODUCT)
    payload["review_date"] = None
    product_id = create_product(client, admin, payload).json()["id"]

    response = client.post(
        f"{API}/admin/products/{product_id}/mark-trackable", headers=admin_headers(admin)
    )
    assert response.status_code == 422
    blockers = {item["field"] for item in response.json()["error"]["details"]["blockers"]}
    assert "review_date" in blockers


def test_farmers_only_see_trackable_products(client, admin_factory, user_factory):
    admin = admin_factory()
    farmer = user_factory(phone="+250788400001")
    draft_id = create_product(client, admin, {"name_en": "Draft only"}).json()["id"]
    live_id = create_product(client, admin).json()["id"]
    client.post(f"{API}/admin/products/{live_id}/mark-trackable", headers=admin_headers(admin))

    from tests.conftest import user_headers

    headers = user_headers(farmer)
    listing = client.get(f"{API}/products", headers=headers).json()
    names = [item["name_en"] for item in listing["items"]]
    assert "Test product" in names
    assert "Draft only" not in names

    hidden = client.get(f"{API}/products/{draft_id}", headers=headers)
    assert hidden.status_code == 404
    assert hidden.json()["error"]["code"] == "product.not_found"


def test_updating_a_trackable_product_creates_a_version(client, admin_factory, db):
    admin = admin_factory()
    product_id = create_product(client, admin).json()["id"]
    client.post(f"{API}/admin/products/{product_id}/mark-trackable", headers=admin_headers(admin))

    updated = client.put(
        f"{API}/admin/products/{product_id}",
        json={"description_en": "A revised description."},
        headers=admin_headers(admin),
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 2

    versions = (
        db.execute(
            text("SELECT version FROM product_versions WHERE product_id = :id ORDER BY version"),
            {"id": product_id},
        )
        .scalars()
        .all()
    )
    assert 1 in versions


def test_updating_a_draft_does_not_bump_the_version(client, admin_factory):
    admin = admin_factory()
    product_id = create_product(client, admin).json()["id"]
    updated = client.put(
        f"{API}/admin/products/{product_id}",
        json={"description_en": "Draft revision."},
        headers=admin_headers(admin),
    )
    assert updated.json()["version"] == 1


def test_pause_and_retire_change_visibility(client, admin_factory, user_factory):
    admin = admin_factory()
    farmer = user_factory(phone="+250788400002")
    product_id = create_product(client, admin).json()["id"]
    client.post(f"{API}/admin/products/{product_id}/mark-trackable", headers=admin_headers(admin))

    from tests.conftest import user_headers

    paused = client.post(f"{API}/admin/products/{product_id}/pause", headers=admin_headers(admin))
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"
    assert (
        client.get(f"{API}/products/{product_id}", headers=user_headers(farmer)).status_code == 404
    )

    retired = client.post(f"{API}/admin/products/{product_id}/retire", headers=admin_headers(admin))
    assert retired.json()["status"] == "retired"

    revive = client.post(
        f"{API}/admin/products/{product_id}/mark-trackable", headers=admin_headers(admin)
    )
    assert revive.status_code == 422
    assert revive.json()["error"]["code"] == "product.retired"


def test_cannot_pause_a_draft(client, admin_factory):
    admin = admin_factory()
    product_id = create_product(client, admin, {"name_en": "Not live"}).json()["id"]
    response = client.post(f"{API}/admin/products/{product_id}/pause", headers=admin_headers(admin))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "product.not_trackable"


def test_status_table_reports_blockers_and_farmers(client, admin_factory):
    admin = admin_factory()
    create_product(client, admin, {"name_en": "Blocked product"})
    response = client.get(f"{API}/admin/products/status-table", headers=admin_headers(admin))
    assert response.status_code == 200
    rows = {item["name_en"]: item for item in response.json()["items"]}
    blocked = rows["Blocked product"]
    assert blocked["blockers"]
    assert blocked["farmers_planting"] == 0


def test_weekly_schedule_task_needs_a_weekday(client, admin_factory):
    admin = admin_factory()
    payload = dict(COMPLETE_PRODUCT)
    payload["schedule_tasks"] = [
        {
            "task_type": "check",
            "day_offset": 1,
            "repeat_rule": "weekly",
            "time_of_day": "morning",
            "instruction_text": "Check the bags.",
        }
    ]
    response = create_product(client, admin, payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "product.schedule.weekday_required"


def test_custom_schedule_task_needs_an_interval(client, admin_factory):
    admin = admin_factory()
    payload = dict(COMPLETE_PRODUCT)
    payload["schedule_tasks"] = [
        {
            "task_type": "check",
            "day_offset": 1,
            "repeat_rule": "custom",
            "time_of_day": "morning",
            "instruction_text": "Check the bags.",
        }
    ]
    response = create_product(client, admin, payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "product.schedule.interval_required"


def test_seeded_mushroom_is_a_draft_and_not_trackable(db, seeded_food_items):
    product = seed_mushroom_product(db)
    assert product.status is ProductStatus.DRAFT
    assert product.needs_expert_review is True
    assert product.growing_steps_validated is False
    assert product.reviewer_name is None
    assert product.conditions_diseases is None

    # Benefits exist as rows but carry no health text.
    assert product.benefits
    assert all(benefit.benefit_text is None for benefit in product.benefits)
    assert not product.nutrients

    # Growing steps are marked as indicative.
    assert product.schedule_tasks
    assert all("validate with RAB" in task.instruction_text for task in product.schedule_tasks)

    blockers = product_service.trackable_blockers(product)
    assert blockers, "the seeded draft must not be publishable"


def test_seeding_twice_does_not_duplicate_the_product(db, seeded_food_items):
    first = seed_mushroom_product(db)
    second = seed_mushroom_product(db)
    assert first.id == second.id
    count = db.scalar(text("SELECT count(*) FROM products WHERE name_en = 'Oyster mushroom'"))
    assert count == 1


def test_seeded_mushroom_schedule_uses_every_required_type(db, seeded_food_items):
    product = seed_mushroom_product(db)
    types = {task.task_type for task in product.schedule_tasks}
    assert TaskType.IRRIGATION in types
    assert TaskType.HARVEST in types
    assert TaskType.CHECK in types
    assert TaskType.SPRAY_OR_TREATMENT in types


def test_product_edit_writes_an_audit_entry(client, admin_factory):
    admin = admin_factory()
    product_id = create_product(client, admin, {"name_en": "Audited"}).json()["id"]
    client.put(
        f"{API}/admin/products/{product_id}",
        json={"name_rw": "Izina"},
        headers=admin_headers(admin),
    )
    log = client.get(
        f"{API}/admin/audit-log",
        params={"action": "product.update"},
        headers=admin_headers(admin),
    ).json()
    assert log["pagination"]["total"] >= 1


def test_content_editor_can_manage_products(client, admin_factory):
    from app.models.enums import AdminRole

    editor = admin_factory(role=AdminRole.CONTENT_EDITOR)
    response = create_product(client, editor, {"name_en": "Editor product"})
    assert response.status_code == 201


def test_support_agent_cannot_create_products(client, admin_factory):
    from app.models.enums import AdminRole

    agent = admin_factory(role=AdminRole.SUPPORT_AGENT)
    response = create_product(client, agent, {"name_en": "Not allowed"})
    assert response.status_code == 403


def test_harvest_max_below_min_is_rejected(client, admin_factory):
    admin = admin_factory()
    payload = dict(COMPLETE_PRODUCT)
    payload["time_to_harvest_min_days"] = 30
    payload["time_to_harvest_max_days"] = 10
    response = create_product(client, admin, payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
