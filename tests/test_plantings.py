"""Plantings, harvests, province context, support requests and monitoring."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.models.enums import (
    AdminRole,
    FundingStatus,
)
from tests.conftest import API, admin_headers, store_questionnaire, user_headers
from tests.test_products import COMPLETE_PRODUCT, create_product


@pytest.fixture
def live_product(client, admin_factory, seeded_food_items, db):
    admin = admin_factory(role=AdminRole.SUPER_ADMIN)
    # Link the product to a food item so the province context has something to join on.
    food_id = db.scalar(text("SELECT id FROM food_items WHERE source_variable = 'h25_21'"))
    db.rollback()
    payload = dict(COMPLETE_PRODUCT)
    payload["food_item_id"] = str(food_id)
    product_id = create_product(client, admin, payload).json()["id"]
    published = client.post(
        f"{API}/admin/products/{product_id}/mark-trackable", headers=admin_headers(admin)
    )
    assert published.status_code == 200
    return product_id, admin


def test_planting_copies_the_schedule_into_tasks(client, live_product, user_factory):
    product_id, _admin = live_product
    farmer = user_factory(phone="+250788600001")

    response = client.post(
        f"{API}/plantings",
        json={"product_id": product_id, "quantity": 2},
        headers=user_headers(farmer),
    )
    assert response.status_code == 201
    body = response.json()
    assert len(body["first_tasks"]) == 3
    assert body["expected_harvest_on"] is not None
    assert body["planting"]["quantity"] == 2
    # Daily irrigation from day 0 up to day 30 is about 31 tasks.
    assert body["planting"]["progress_percent"] == 0


def test_first_three_tasks_are_the_earliest(client, live_product, user_factory, db):
    product_id, _admin = live_product
    farmer = user_factory(phone="+250788600002")
    body = client.post(
        f"{API}/plantings",
        json={"product_id": product_id},
        headers=user_headers(farmer),
    ).json()

    due_dates = [task["due_at"] for task in body["first_tasks"]]
    assert due_dates == sorted(due_dates)

    total = db.scalar(
        text("SELECT count(*) FROM task_instances WHERE planting_id = :id"),
        {"id": body["planting"]["id"]},
    )
    db.rollback()
    assert total > 3


def test_cannot_plant_a_draft_product(client, admin_factory, user_factory):
    admin = admin_factory()
    farmer = user_factory(phone="+250788600003")
    draft_id = create_product(client, admin, {"name_en": "Still a draft"}).json()["id"]

    response = client.post(
        f"{API}/plantings",
        json={"product_id": draft_id},
        headers=user_headers(farmer),
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "product.not_found"


def test_planting_list_and_detail(client, live_product, user_factory):
    product_id, _admin = live_product
    farmer = user_factory(phone="+250788600004")
    headers = user_headers(farmer)
    created = client.post(
        f"{API}/plantings", json={"product_id": product_id}, headers=headers
    ).json()

    listing = client.get(f"{API}/plantings", headers=headers).json()
    assert listing["pagination"]["total"] == 1
    assert listing["items"][0]["product_name_en"] == "Test product"

    detail = client.get(f"{API}/plantings/{created['planting']['id']}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["tasks"]


def test_finish_a_planting_closes_its_tasks(client, live_product, user_factory, db):
    product_id, _admin = live_product
    farmer = user_factory(phone="+250788600005")
    headers = user_headers(farmer)
    planting_id = client.post(
        f"{API}/plantings", json={"product_id": product_id}, headers=headers
    ).json()["planting"]["id"]

    finished = client.patch(
        f"{API}/plantings/{planting_id}", json={"status": "finished"}, headers=headers
    )
    assert finished.status_code == 200
    assert finished.json()["status"] == "finished"

    pending = db.scalar(
        text(
            "SELECT count(*) FROM task_instances "
            "WHERE planting_id = :id AND status IN ('pending', 'snoozed')"
        ),
        {"id": planting_id},
    )
    db.rollback()
    assert pending == 0


def test_one_farmer_cannot_read_another_planting(client, live_product, user_factory):
    product_id, _admin = live_product
    first = user_factory(phone="+250788600006")
    second = user_factory(phone="+250788600007")
    planting_id = client.post(
        f"{API}/plantings", json={"product_id": product_id}, headers=user_headers(first)
    ).json()["planting"]["id"]

    response = client.get(f"{API}/plantings/{planting_id}", headers=user_headers(second))
    assert response.status_code == 404


def test_harvest_is_recorded_and_summarised(client, live_product, user_factory):
    product_id, _admin = live_product
    farmer = user_factory(phone="+250788600008")
    headers = user_headers(farmer)
    planting_id = client.post(
        f"{API}/plantings", json={"product_id": product_id}, headers=headers
    ).json()["planting"]["id"]

    for used_for, amount in (("eaten", 2.5), ("sold", 4.0), ("preserved", 1.5)):
        response = client.post(
            f"{API}/harvests",
            json={"planting_id": planting_id, "quantity_kg": amount, "used_for": used_for},
            headers=headers,
        )
        assert response.status_code == 201

    summary = client.get(f"{API}/harvests/summary", headers=headers).json()
    assert summary["count"] == 3
    assert summary["total_kg"] == pytest.approx(8.0)
    assert summary["by_use"]["sold"] == pytest.approx(4.0)


def test_province_context_is_hidden_below_the_threshold(
    client, live_product, user_factory, db, seeded_food_items
):
    """A province cell with fewer than 10 households must not be shown."""
    product_id, _admin = live_product
    farmer = user_factory(phone="+250788600009")
    farmer.province = "Kigali City"
    db.commit()

    from app.models.analysis import ProvinceFoodSummary
    from app.models.enums import GeoLevel
    from app.models.survey import Survey

    survey = Survey(
        name="Context census",
        year=2022,
        original_filename="c.dta",
        stored_path="c/c.dta",
        file_size_bytes=1,
        checksum_sha256="e" * 64,
        status="ready",
    )
    db.add(survey)
    db.commit()
    store_questionnaire(db, survey.id)

    food_id = db.scalar(
        text("SELECT food_item_id FROM products WHERE id = :id"), {"id": product_id}
    )
    db.rollback()
    db.add(
        ProvinceFoodSummary(
            survey_id=survey.id,
            food_item_id=food_id,
            level=GeoLevel.PROVINCE,
            province="Kigali City",
            households_total=200,
            weighted_households_total=2000,
            households_with_food=4,
            weighted_households_with_food=40,
        )
    )
    db.commit()

    body = client.get(f"{API}/products/{product_id}", headers=user_headers(farmer)).json()
    context = body["province_context"]
    assert context["suppressed"] is True
    assert context["available"] is False
    assert context["share"] is None
    assert context["translation_key"] == "analysis.fewer_than_threshold"


def test_province_context_is_shown_above_the_threshold(
    client, live_product, user_factory, db, seeded_food_items
):
    product_id, _admin = live_product
    farmer = user_factory(phone="+250788600010")
    farmer.province = "Northern"
    db.commit()

    from app.models.analysis import ProvinceFoodSummary
    from app.models.enums import GeoLevel
    from app.models.survey import Survey

    survey = Survey(
        name="Context census 2",
        year=2022,
        original_filename="d.dta",
        stored_path="d/d.dta",
        file_size_bytes=1,
        checksum_sha256="f" * 64,
        status="ready",
    )
    db.add(survey)
    db.commit()
    store_questionnaire(db, survey.id)

    food_id = db.scalar(
        text("SELECT food_item_id FROM products WHERE id = :id"), {"id": product_id}
    )
    db.rollback()
    db.add(
        ProvinceFoodSummary(
            survey_id=survey.id,
            food_item_id=food_id,
            level=GeoLevel.PROVINCE,
            province="Northern",
            households_total=200,
            weighted_households_total=2000,
            households_with_food=50,
            weighted_households_with_food=500,
        )
    )
    db.commit()

    body = client.get(f"{API}/products/{product_id}", headers=user_headers(farmer)).json()
    context = body["province_context"]
    assert context["available"] is True
    assert context["share"] == pytest.approx(0.25, rel=1e-4)
    assert context["source_year"] == 2022
    assert context["source_name"]


def test_specialist_request_flow(client, user_factory, admin_factory):
    farmer = user_factory(phone="+250788600011")
    supporter = admin_factory(role=AdminRole.SUPPORT_AGENT)

    created = client.post(
        f"{API}/specialist-requests",
        json={
            "specialist_type": "agronomist",
            "description": "My bags are turning green.",
        },
        headers=user_headers(farmer),
    )
    assert created.status_code == 201
    assert created.json()["status"] == "new"
    request_id = created.json()["id"]

    board = client.get(f"{API}/admin/specialist-requests", headers=admin_headers(supporter))
    assert board.status_code == 200
    assert board.json()["pagination"]["total"] == 1

    updated = client.patch(
        f"{API}/admin/specialist-requests/{request_id}",
        json={"status": "assigned", "assigned_to": "Jean"},
        headers=admin_headers(supporter),
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "assigned"

    contacted = client.patch(
        f"{API}/admin/specialist-requests/{request_id}",
        json={"status": "contacted"},
        headers=admin_headers(supporter),
    )
    assert contacted.json()["contacted_at"] is not None

    mine = client.get(f"{API}/specialist-requests", headers=user_headers(farmer)).json()
    assert mine["items"][0]["status"] == "contacted"


def test_funding_interest_requires_consent(client, user_factory):
    farmer = user_factory(phone="+250788600012")
    response = client.post(
        f"{API}/funding-interest",
        json={
            "planned_size": "200 bags",
            "location": "Musanze",
            "building_or_land": "existing shed",
            "consent_to_share": False,
        },
        headers=user_headers(farmer),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_refer_to_brd_needs_consent_and_sets_the_status(client, user_factory, admin_factory):
    farmer = user_factory(phone="+250788600013")
    supporter = admin_factory(role=AdminRole.SUPPORT_AGENT)

    created = client.post(
        f"{API}/funding-interest",
        json={
            "planned_size": "500 bags",
            "location": "Rubavu",
            "building_or_land": "new greenhouse",
            "funding_needed": 900000,
            "consent_to_share": True,
        },
        headers=user_headers(farmer),
    )
    assert created.status_code == 201
    assert created.json()["consent_at"] is not None
    interest_id = created.json()["id"]

    referred = client.post(
        f"{API}/admin/funding-interest/{interest_id}/refer-to-brd",
        json={"brd_reference": "BRD-2026-001"},
        headers=admin_headers(supporter),
    )
    assert referred.status_code == 200
    assert referred.json()["status"] == "referred_to_brd"
    assert referred.json()["brd_reference"] == "BRD-2026-001"

    mine = client.get(f"{API}/funding-interest", headers=user_headers(farmer)).json()
    assert mine["items"][0]["status"] == "referred_to_brd"


def test_refer_to_brd_is_blocked_without_consent(client, user_factory, admin_factory, db):
    farmer = user_factory(phone="+250788600014")
    supporter = admin_factory(role=AdminRole.SUPPORT_AGENT)

    from app.models.farming import FundingInterest

    record = FundingInterest(
        user_id=farmer.id,
        planned_size="10 bags",
        location="Huye",
        building_or_land="land",
        consent_to_share=False,
        status=FundingStatus.NEW,
    )
    db.add(record)
    db.commit()

    response = client.post(
        f"{API}/admin/funding-interest/{record.id}/refer-to-brd",
        json={},
        headers=admin_headers(supporter),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "funding.no_consent"


def test_message_templates_are_seeded_in_three_languages(client, admin_factory, db):
    from app.sms.templates import seed_templates

    seed_templates(db)
    admin = admin_factory()
    response = client.get(
        f"{API}/admin/message-templates",
        params={"key": "task.reminder.morning"},
        headers=admin_headers(admin),
    )
    assert response.status_code == 200
    languages = {item["language"] for item in response.json()["items"]}
    assert languages == {"rw", "en", "fr"}


def test_sms_template_over_160_characters_is_rejected(client, admin_factory):
    admin = admin_factory()
    response = client.put(
        f"{API}/admin/message-templates/task.reminder.morning/en",
        json={"body": "In app text", "sms_body": "x" * 200},
        headers=admin_headers(admin),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "templates.sms_too_long"


def test_monitoring_overview_and_funnel(client, admin_factory, live_product, user_factory):
    product_id, _admin = live_product
    admin = admin_factory(role=AdminRole.DATA_ANALYST)
    farmer = user_factory(phone="+250788600015")
    client.post(f"{API}/plantings", json={"product_id": product_id}, headers=user_headers(farmer))

    overview = client.get(f"{API}/admin/monitoring/overview", headers=admin_headers(admin))
    assert overview.status_code == 200
    body = overview.json()
    assert body["plantings"]["total"] >= 1
    assert body["products"]["trackable"] >= 1

    funnel = client.get(f"{API}/admin/monitoring/funnel", headers=admin_headers(admin)).json()
    stages = {stage["key"]: stage["count"] for stage in funnel["stages"]}
    assert stages["signed_up"] >= 1
    assert stages["planted"] >= 1


def test_threshold_setting_is_validated_and_applied(client, admin_factory):
    admin = admin_factory()
    bad = client.put(
        f"{API}/admin/settings/privacy.small_cell_threshold",
        json={"value": 0},
        headers=admin_headers(admin),
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "settings.invalid_threshold"

    good = client.put(
        f"{API}/admin/settings/privacy.small_cell_threshold",
        json={"value": 15},
        headers=admin_headers(admin),
    )
    assert good.status_code == 200
    assert good.json()["value"] == 15


def test_run_reminders_endpoint(client, admin_factory):
    admin = admin_factory(role=AdminRole.DATA_ANALYST)
    response = client.post(f"{API}/admin/monitoring/run-reminders", headers=admin_headers(admin))
    assert response.status_code == 200
    assert "reminders_created" in response.json()


def test_partners_are_published(client, user_factory):
    farmer = user_factory(phone="+250788600016")
    response = client.get(f"{API}/partners", headers=user_headers(farmer))
    assert response.status_code == 200
    codes = {partner["code"] for partner in response.json()["partners"]}
    assert codes == {"RAB", "BRD"}
