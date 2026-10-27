"""Analysis API: suppression in responses, filters, permissions and pagination."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text

from app.models.enums import AdminRole, FoodGroup
from app.models.household import FoodItem, Household
from app.models.survey import FieldMapping, Survey
from app.services import settings_service
from tests.conftest import (
    API,
    admin_headers,
    confirm_mapping,
    store_questionnaire,
    upload_survey,
)

ANALYSIS_VARIABLES = {"h25_21": "1", "h24_01": "1", "h25_01": "1"}


@pytest.fixture
def loaded_survey(db, admin_factory):
    """A survey with confirmed mappings so aggregation can run."""
    admin = admin_factory(role=AdminRole.DATA_ANALYST)
    survey = Survey(
        name="API census",
        year=2022,
        original_filename="api.dta",
        stored_path="api/api.dta",
        file_size_bytes=1,
        checksum_sha256="b" * 64,
        status="ready",
    )
    db.add(survey)
    db.commit()

    for variable, yes_code in ANALYSIS_VARIABLES.items():
        db.add(
            FieldMapping(
                survey_id=survey.id,
                variable_name=variable,
                platform_group="vegetables",
                yes_code=yes_code,
                yes_label="Yes",
                confirmed=True,
            )
        )
    for name, variable, group in (
        ("Mushroom", "h25_21", FoodGroup.FUNGI),
        ("Maize", "h24_01", FoodGroup.FIELD_CROP),
        ("Amaranths", "h25_01", FoodGroup.VEGETABLE),
    ):
        exists = db.scalar(
            text("SELECT 1 FROM food_items WHERE source_variable = :v"), {"v": variable}
        )
        if not exists:
            db.add(FoodItem(standard_name=name, group=group, source_variable=variable))
    store_questionnaire(db, survey.id)
    return survey, admin


def add_households(db, survey, *, province, district, sector, count, mushroom_count, weight=10):
    for index in range(count):
        db.add(
            Household(
                survey_id=survey.id,
                hhid=f"{province[:3]}{district[:3]}{sector[-1]}{index:03d}",
                ml01=province,
                ml02=district,
                ml03=sector,
                ml07="Urban",
                hh_weight=Decimal(str(weight)),
                household_size=4,
                attributes={"h25_21": 1 if index < mushroom_count else 2, "h24_01": 1},
            )
        )
    db.commit()


def run_aggregation(db, survey):
    from app.analysis import aggregation

    return aggregation.run_aggregation(db, survey)


def test_food_summary_releases_cells_at_or_above_the_threshold(client, db, loaded_survey):
    survey, admin = loaded_survey
    add_households(
        db,
        survey,
        province="Kigali City",
        district="Gasabo",
        sector="Sector A",
        count=12,
        mushroom_count=10,
    )
    run_aggregation(db, survey)

    response = client.get(
        f"{API}/admin/analysis/province-food-summary",
        params={"level": "sector", "survey_id": str(survey.id)},
        headers=admin_headers(admin),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["small_cell_threshold"] == 10
    assert body["meta"]["source_year"] == 2022
    mushroom = next(item for item in body["items"] if item["standard_name"] == "Mushroom")
    assert mushroom["share"]["suppressed"] is False
    assert mushroom["share"]["value"] == pytest.approx(10 / 12, rel=1e-4)
    assert mushroom["households_with_food"]["count"] == 10


def test_cells_below_the_threshold_are_withheld_everywhere(client, db, loaded_survey):
    survey, admin = loaded_survey
    add_households(
        db,
        survey,
        province="Northern",
        district="Burera",
        sector="Sector B",
        count=20,
        mushroom_count=3,
    )
    run_aggregation(db, survey)

    body = client.get(
        f"{API}/admin/analysis/province-food-summary",
        params={"level": "sector", "province": "Northern", "survey_id": str(survey.id)},
        headers=admin_headers(admin),
    ).json()
    mushroom = next(item for item in body["items"] if item["standard_name"] == "Mushroom")

    for field in ("share", "unweighted_share", "households_with_food"):
        assert mushroom[field]["suppressed"] is True
        assert mushroom[field]["value"] is None
        assert mushroom[field]["count"] is None
        assert mushroom[field]["translation_key"] == "analysis.fewer_than_threshold"


def test_threshold_change_takes_effect_without_rerunning_the_pipeline(client, db, loaded_survey):
    survey, admin = loaded_survey
    add_households(
        db,
        survey,
        province="Northern",
        district="Burera",
        sector="Sector B",
        count=20,
        mushroom_count=3,
    )
    run_aggregation(db, survey)

    settings_service.set_value(db, "privacy.small_cell_threshold", 3)
    body = client.get(
        f"{API}/admin/analysis/province-food-summary",
        params={"level": "sector", "province": "Northern", "survey_id": str(survey.id)},
        headers=admin_headers(admin),
    ).json()
    mushroom = next(item for item in body["items"] if item["standard_name"] == "Mushroom")
    assert mushroom["share"]["suppressed"] is False
    assert body["meta"]["small_cell_threshold"] == 3


def test_mushroom_endpoint_reports_counts_shares_and_breakdowns(client, db, loaded_survey):
    survey, admin = loaded_survey
    add_households(
        db,
        survey,
        province="Kigali City",
        district="Gasabo",
        sector="Sector A",
        count=40,
        mushroom_count=12,
    )
    run_aggregation(db, survey)

    body = client.get(
        f"{API}/admin/analysis/mushroom",
        params={"level": "national", "survey_id": str(survey.id)},
        headers=admin_headers(admin),
    ).json()
    cell = body["items"][0]
    assert cell["households_total"]["count"] == 40
    assert cell["mushroom_households"]["count"] == 12
    assert cell["share"]["value"] == pytest.approx(0.3, rel=1e-4)
    assert cell["urban_share_of_mushroom"]["value"] == pytest.approx(1.0, rel=1e-4)
    # No head information was loaded, so the female headed share is unknown.
    assert cell["female_headed_share"]["suppressed"] is True


def test_diversity_and_readiness_endpoints_return_rows(client, db, loaded_survey):
    survey, admin = loaded_survey
    add_households(
        db,
        survey,
        province="Kigali City",
        district="Gasabo",
        sector="Sector A",
        count=15,
        mushroom_count=5,
    )
    run_aggregation(db, survey)

    diversity = client.get(
        f"{API}/admin/analysis/diversity",
        params={"level": "national", "survey_id": str(survey.id)},
        headers=admin_headers(admin),
    )
    assert diversity.status_code == 200
    assert diversity.json()["items"][0]["households_total"]["count"] == 15

    readiness = client.get(
        f"{API}/admin/analysis/readiness",
        params={"level": "national", "survey_id": str(survey.id)},
        headers=admin_headers(admin),
    )
    assert readiness.status_code == 200
    item = readiness.json()["items"][0]
    assert item["weights"]
    assert "score" in item
    # The fixture stores value labels, so every resolvable component says so.
    sources = {
        name: component["definition_source"] for name, component in item["components"].items()
    }
    assert sources.get("electricity") == "value_labels"
    assert item["components"]["household_size"]["households"] == 15


def test_analysis_requires_the_analysis_permission(client, db, loaded_survey, admin_factory):
    survey, _ = loaded_survey
    add_households(
        db,
        survey,
        province="Kigali City",
        district="Gasabo",
        sector="Sector A",
        count=12,
        mushroom_count=1,
    )
    run_aggregation(db, survey)

    support = admin_factory(role=AdminRole.SUPPORT_AGENT)
    response = client.get(
        f"{API}/admin/analysis/mushroom",
        params={"survey_id": str(survey.id)},
        headers=admin_headers(support),
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "auth.forbidden"


def test_analysis_without_aggregates_returns_404(client, db, admin_factory):
    admin = admin_factory(role=AdminRole.DATA_ANALYST)
    survey = Survey(
        name="Fresh",
        year=2022,
        original_filename="f.dta",
        stored_path="f/f.dta",
        file_size_bytes=1,
        checksum_sha256="c" * 64,
        status="ready",
    )
    db.add(survey)
    db.commit()

    response = client.get(
        f"{API}/admin/analysis/diversity",
        params={"survey_id": str(survey.id)},
        headers=admin_headers(admin),
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "analysis.not_found"


def test_food_items_can_be_listed_and_filtered(client, db, admin_factory, seeded_food_items):
    admin = admin_factory(role=AdminRole.DATA_ANALYST)
    response = client.get(
        f"{API}/admin/food-items",
        params={"group": "fungi"},
        headers=admin_headers(admin),
    )
    assert response.status_code == 200
    body = response.json()
    assert any(item["standard_name"] == "Mushroom" for item in body["items"])
    assert all(item["group"] == "fungi" for item in body["items"])


def test_food_item_edit_writes_an_audit_entry(client, db, admin_factory, seeded_food_items):
    admin = admin_factory()
    item_id = db.scalar(text("SELECT id FROM food_items WHERE source_variable = 'h25_21'"))
    db.rollback()

    response = client.patch(
        f"{API}/admin/food-items/{item_id}",
        json={"local_name": "Ubunyobwa"},
        headers=admin_headers(admin),
    )
    assert response.status_code == 200
    assert response.json()["local_name"] == "Ubunyobwa"

    log = client.get(
        f"{API}/admin/audit-log",
        params={"action": "food_item.update"},
        headers=admin_headers(admin),
    ).json()
    assert log["pagination"]["total"] == 1


def test_read_only_role_cannot_edit_food_items(client, db, admin_factory, seeded_food_items):
    admin = admin_factory(role=AdminRole.SUPPORT_AGENT)
    item_id = db.scalar(text("SELECT id FROM food_items WHERE source_variable = 'h25_21'"))
    db.rollback()

    response = client.patch(
        f"{API}/admin/food-items/{item_id}",
        json={"local_name": "X"},
        headers=admin_headers(admin),
    )
    assert response.status_code == 403


def test_uploaded_survey_gets_aggregates_from_the_pipeline(
    client, admin_factory, synthetic_dta, db, seeded_food_items
):
    admin = admin_factory(role=AdminRole.DATA_ANALYST)
    headers = admin_headers(admin)
    body = upload_survey(client, headers, synthetic_dta).json()
    confirmed = confirm_mapping(client, headers, body["job"]["id"])
    assert confirmed.status_code == 200

    db.rollback()
    summary = db.execute(
        text("SELECT summary FROM survey_jobs WHERE id = :job_id"),
        {"job_id": body["job"]["id"]},
    ).scalar()
    db.rollback()
    assert "aggregated" in summary
    aggregated = summary["aggregated"]
    # The synthetic file only contains a subset of the reference food variables.
    assert aggregated["food_items_aggregated"] > 0
    assert aggregated["food_summary_cells"] > 0

    response = client.get(
        f"{API}/admin/analysis/mushroom",
        params={"survey_id": body["survey"]["id"], "level": "national"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["items"]


def test_pagination_meta_is_present_on_analysis_lists(client, db, loaded_survey):
    survey, admin = loaded_survey
    add_households(
        db,
        survey,
        province="Kigali City",
        district="Gasabo",
        sector="Sector A",
        count=12,
        mushroom_count=4,
    )
    run_aggregation(db, survey)

    body = client.get(
        f"{API}/admin/analysis/province-food-summary",
        params={"survey_id": str(survey.id), "page_size": 2, "page": 1},
        headers=admin_headers(admin),
    ).json()
    assert body["pagination"]["page"] == 1
    assert body["pagination"]["page_size"] == 2
    assert len(body["items"]) == 2
    assert body["pagination"]["total"] >= 3
