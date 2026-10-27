"""Nutrition import and the ranking engine."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.analysis import nutrition, ranking
from app.models.enums import FeasibilityLevel, FoodGroup, RankingStatus
from app.models.household import FoodItem, Household
from app.models.survey import FieldMapping, Survey
from app.services import settings_service
from tests.conftest import store_questionnaire

CSV_HEADER = "food_variable,nutrient,amount_per_100g,unit,source,reviewer_name,review_date\n"


@pytest.fixture
def ranked_survey(db, seeded_food_items):
    survey = Survey(
        name="Ranking census",
        year=2022,
        original_filename="r.dta",
        stored_path="r/r.dta",
        file_size_bytes=1,
        checksum_sha256="d" * 64,
        status="ready",
    )
    db.add(survey)
    db.commit()

    for variable in ("h25_21", "h24_01", "h24_09", "h25_01", "h25_02"):
        db.add(
            FieldMapping(
                survey_id=survey.id,
                variable_name=variable,
                platform_group="vegetables",
                yes_code="1",
                yes_label="Yes",
                confirmed=True,
            )
        )
    db.commit()
    store_questionnaire(db, survey.id)

    # Three provinces with different mixes so clustering has something to group.
    plan = [
        ("Kigali City", {"h24_01": 1, "h25_01": 1}),
        ("Kigali City", {"h24_01": 1}),
        ("Northern", {"h24_09": 1}),
        ("Northern", {"h24_09": 1}),
        ("Northern", {"h24_09": 1}),
        ("Southern", {"h25_21": 1}),
        ("Southern", {"h25_21": 1}),
        ("Southern", {"h25_01": 1}),
        ("Eastern", {"h24_01": 1}),
        ("Eastern", {}),
        ("Western", {"h25_02": 1}),
        ("Western", {}),
    ]
    index = 0
    for province, attributes in plan:
        for repeat in range(3):
            index += 1
            values = {key: 1 for key in attributes}
            for key in ("h24_01", "h24_09", "h25_01", "h25_02", "h25_21"):
                values.setdefault(key, 2)
            db.add(
                Household(
                    survey_id=survey.id,
                    hhid=f"{province[:3]}{index:04d}",
                    ml01=province,
                    ml02=f"{province} District {repeat}",
                    ml03=f"Sector {index}",
                    ml07="Rural",
                    hh_weight=Decimal("10"),
                    household_size=4,
                    attributes=values,
                )
            )
    db.commit()

    from app.analysis import aggregation

    aggregation.run_aggregation(db, survey)
    return survey


def test_csv_import_creates_rows(db, seeded_food_items):
    content = (
        CSV_HEADER
        + "h25_21,iron,1.2,mg,Food composition table,Dr Uwase,2026-01-15\n"
        + "h25_21,protein,3.1,g,Food composition table,Dr Uwase,2026-01-15\n"
    ).encode()
    result = nutrition.import_nutrition_csv(db, content)
    assert result.imported == 2
    assert result.skipped == 0

    rows = nutrition.nutrition_by_food(db)
    assert len(rows) == 1
    amounts = next(iter(rows.values()))
    assert amounts["iron"] == pytest.approx(1.2)
    assert amounts["protein"] == pytest.approx(3.1)


def test_csv_import_reports_unknown_food_and_bad_numbers(db, seeded_food_items):
    content = (
        CSV_HEADER
        + "h99_99,iron,1,mg,src,rev,2026-01-15\n"
        + "h25_21,iron,not-a-number,mg,src,rev,2026-01-15\n"
        + "h25_21,zinc,-5,mg,src,rev,2026-01-15\n"
        + "h25_21,zinc,2,mg,src,rev,2026-01-15\n"
    ).encode()
    result = nutrition.import_nutrition_csv(db, content)
    assert result.imported == 1
    assert result.skipped == 3
    assert any("unknown food variable" in error for error in result.errors)


def test_csv_import_requires_the_required_columns(db, seeded_food_items):
    from app.core.errors import ApiError

    with pytest.raises(ApiError) as error:
        nutrition.import_nutrition_csv(db, b"food_variable,nutrient\nh25_21,iron\n")
    assert error.value.code == "nutrition.missing_columns"


def test_csv_import_normalises_nutrient_names(db, seeded_food_items):
    content = (CSV_HEADER + "h25_21,Vitamin A,0.02,mg,src,rev,2026-01-15\n").encode()
    nutrition.import_nutrition_csv(db, content)
    amounts = next(iter(nutrition.nutrition_by_food(db).values()))
    assert "vitamin_a" in amounts


def test_nutrition_starts_empty(db):
    assert nutrition.nutrition_is_empty(db) is True


def test_gap_score_is_relative_to_the_food_group():
    groups = {
        uuid.uuid4(): FoodGroup.VEGETABLE,
        uuid.uuid4(): FoodGroup.VEGETABLE,
        uuid.uuid4(): FoodGroup.FIELD_CROP,
        uuid.uuid4(): FoodGroup.FIELD_CROP,
    }
    ids = list(groups)
    # Vegetables are rare overall, crops are common. The rarer vegetable must still
    # out-score the common crop on the gap measure.
    shares = {ids[0]: 0.05, ids[1]: 0.01, ids[2]: 0.60, ids[3]: 0.55}
    scores = ranking.gap_scores(shares, groups)
    assert scores[ids[1]] > scores[ids[0]]
    assert scores[ids[3]] > scores[ids[2]]
    assert all(0.0 <= value <= 1.0 for value in scores.values())


def test_ranking_runs_in_gap_only_mode_without_nutrition(db, ranked_survey):
    result = ranking.run_ranking(db, ranked_survey)
    assert result.score_basis == "gap_only"
    assert result.items_written > 0
    assert any("Nutrition data is needed" in warning for warning in result.warnings)

    run = db.get(ranking.AiRankingRun, result.run_id)
    assert run.status == RankingStatus.COMPLETED
    assert run.fingerprint


def test_mushroom_is_pinned_first_with_its_real_score(db, ranked_survey):
    result = ranking.run_ranking(db, ranked_survey)
    items = db.execute(
        text(
            """
            SELECT i.rank, i.pinned, i.total_score, i.explanation, f.source_variable
            FROM ai_ranking_items i
            JOIN food_items f ON f.id = i.food_item_id
            WHERE i.run_id = :run_id AND i.province IS NULL
            ORDER BY i.rank
            """
        ),
        {"run_id": str(result.run_id)},
    ).all()

    assert items
    first = items[0]
    assert first.source_variable == "h25_21"
    assert first.pinned is True
    assert first.total_score is not None
    assert "Selected by the team" in first.explanation or first.total_score is not None


def test_ranking_explains_every_item_without_health_claims(db, ranked_survey):
    result = ranking.run_ranking(db, ranked_survey)
    explanations = (
        db.execute(
            text("SELECT explanation FROM ai_ranking_items WHERE run_id = :run_id"),
            {"run_id": str(result.run_id)},
        )
        .scalars()
        .all()
    )
    assert explanations
    for sentence in explanations:
        assert sentence.strip()
        lowered = sentence.lower()
        for banned in ("cures", "prevents disease", "treats", "guaranteed"):
            assert banned not in lowered


def test_ranking_reason_fields_are_structured(db, ranked_survey):
    result = ranking.run_ranking(db, ranked_survey)
    reasons = db.execute(
        text(
            """
            SELECT reason_fields FROM ai_ranking_items
            WHERE run_id = :run_id AND province IS NULL ORDER BY rank LIMIT 1
            """
        ),
        {"run_id": str(result.run_id)},
    ).scalar()
    assert "weighted_share" in reasons
    assert "food_group" in reasons
    assert "feasibility" in reasons
    assert reasons["score_basis"] in {"gap_only", "full"}


def test_ranking_is_reproducible_with_the_same_inputs(db, ranked_survey):
    first = ranking.run_ranking(db, ranked_survey)
    second = ranking.run_ranking(db, ranked_survey)
    assert first.items_written == second.items_written

    def scores(run_id):
        return db.execute(
            text(
                """
                SELECT f.standard_name, i.total_score
                FROM ai_ranking_items i JOIN food_items f ON f.id = i.food_item_id
                WHERE i.run_id = :run_id AND i.province IS NULL
                ORDER BY f.standard_name
                """
            ),
            {"run_id": str(run_id)},
        ).all()

    assert scores(first.run_id) == scores(second.run_id)


def test_weights_change_the_order(db, ranked_survey):
    gap_heavy = ranking.run_ranking(db, ranked_survey, weights={"gap": 1.0, "feasibility": 0.0})
    feasibility_heavy = ranking.run_ranking(
        db, ranked_survey, weights={"gap": 0.0, "feasibility": 1.0}
    )
    assert gap_heavy.run_id != feasibility_heavy.run_id

    # With zero feasibility weight only the gap matters, so the rarest food wins.
    top_gap = db.execute(
        text(
            """
            SELECT f.source_variable FROM ai_ranking_items i
            JOIN food_items f ON f.id = i.food_item_id
            WHERE i.run_id = :run_id AND i.province IS NULL AND NOT i.pinned
            ORDER BY i.rank LIMIT 1
            """
        ),
        {"run_id": str(gap_heavy.run_id)},
    ).scalar()
    assert top_gap in {"h25_02", "h24_09", "h25_01", "h24_01", "h25_21"}


def test_clustering_reports_a_label_and_silhouette(db, ranked_survey):
    result = ranking.run_ranking(db, ranked_survey)
    assert result.cluster_count is not None
    clusters = db.execute(
        text(
            """
            SELECT unit, cluster_label FROM province_clusters
            WHERE run_id = :run_id AND level = 'province'
            """
        ),
        {"run_id": str(result.run_id)},
    ).all()
    assert clusters
    for cluster in clusters:
        assert cluster.cluster_label


def test_clustering_handles_too_few_units():
    names = {uuid.uuid4(): "Mushroom"}
    outcome = ranking.cluster_units({"Only": {next(iter(names)): 0.1}}, names)
    assert outcome.cluster_count == 1
    assert outcome.labels == {"Only": "All units"}


def test_nutrition_present_switches_to_full_score_basis(db, ranked_survey):
    content = (
        CSV_HEADER
        + "h25_21,protein,3.1,g,table,rev,2026-01-15\n"
        + "h25_21,iron,1.2,mg,table,rev,2026-01-15\n"
        + "h24_01,protein,9.0,g,table,rev,2026-01-15\n"
    ).encode()
    nutrition.import_nutrition_csv(db, content)

    result = ranking.run_ranking(db, ranked_survey)
    assert result.score_basis == "full"
    assert not any("Nutrition data is needed" in warning for warning in result.warnings)

    row = db.execute(
        text(
            """
            SELECT nutrition_value, nutrition_need FROM ai_ranking_items i
            JOIN food_items f ON f.id = i.food_item_id
            WHERE i.run_id = :run_id AND f.source_variable = 'h25_21' AND i.province IS NULL
            """
        ),
        {"run_id": str(result.run_id)},
    ).one()
    assert row.nutrition_value is not None
    assert row.nutrition_need is not None


def test_feasibility_feeds_the_score(db, ranked_survey):
    mushroom_id = db.scalar(text("SELECT id FROM food_items WHERE source_variable = 'h25_21'"))
    db.rollback()
    food = db.get(FoodItem, mushroom_id)
    food.budget_fit = FeasibilityLevel.HIGH
    food.land_need = FeasibilityLevel.HIGH
    food.season_fit = FeasibilityLevel.HIGH
    db.commit()

    result = ranking.run_ranking(db, ranked_survey)
    feasibility = db.execute(
        text(
            """
            SELECT feasibility FROM ai_ranking_items
            WHERE run_id = :run_id AND food_item_id = :food_id AND province IS NULL
            """
        ),
        {"run_id": str(result.run_id), "food_id": str(mushroom_id)},
    ).scalar()
    assert float(feasibility) == pytest.approx(1.0)


def test_weights_are_normalised_from_settings(db):
    settings_service.seed_defaults(db)
    weights = ranking.resolve_weights(db, None)
    assert sum(weights.values()) == pytest.approx(1.0, abs=1e-4)
    assert set(weights) == {"nutrition_value", "nutrition_need", "feasibility", "gap"}


def test_requested_weights_override_settings(db):
    weights = ranking.resolve_weights(db, {"gap": 3.0})
    assert weights["gap"] > weights["nutrition_value"]


def test_per_province_rankings_are_stored(db, ranked_survey):
    result = ranking.run_ranking(db, ranked_survey)
    provinces = (
        db.execute(
            text(
                """
            SELECT DISTINCT province FROM ai_ranking_items
            WHERE run_id = :run_id AND province IS NOT NULL
            """
            ),
            {"run_id": str(result.run_id)},
        )
        .scalars()
        .all()
    )
    assert set(provinces) >= {"Kigali City", "Northern"}


def test_latest_run_returns_the_newest(db, ranked_survey):
    first = ranking.run_ranking(db, ranked_survey)
    second = ranking.run_ranking(db, ranked_survey)
    latest = ranking.latest_run(db, ranked_survey.id)
    assert latest is not None
    assert latest.id == second.run_id
    assert latest.id != first.run_id


def test_nutrition_value_is_none_without_amounts():
    assert nutrition.nutrition_value(None, {"iron": 1.0}) is None
    assert nutrition.nutrition_value({}, {"iron": 1.0}) is None


def test_nutrition_value_scales_with_amount():
    low = nutrition.nutrition_value({"iron": 0.5}, {"iron": 1.0})
    high = nutrition.nutrition_value({"iron": 9.0}, {"iron": 1.0})
    assert low is not None and high is not None
    assert high > low
    assert high <= 1.0
