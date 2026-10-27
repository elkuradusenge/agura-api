"""Aggregation tests with values computed by hand.

A small fixture is inserted directly so every weighted share can be checked against
arithmetic written out in the test.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.analysis import aggregation
from app.analysis.suppression import cell
from app.models.enums import FoodGroup, GeoLevel
from app.models.household import FoodItem, Household
from app.models.survey import FieldMapping, Survey


@pytest.fixture
def survey(db) -> Survey:
    survey = Survey(
        name="Hand computed census",
        year=2022,
        original_filename="hand.dta",
        stored_path="hand/hand.dta",
        file_size_bytes=1,
        checksum_sha256="a" * 64,
        status="ready",
    )
    db.add(survey)
    db.commit()
    db.refresh(survey)
    return survey


@pytest.fixture
def mapping_rows(db, survey):
    """Confirmed mappings for the handful of variables the fixture uses."""
    variables = {
        "h25_21": "1",  # mushroom, yes
        "h24_01": "1",  # maize
        "h24_09": "1",  # cassava
        "h25_01": "1",  # amaranths
        "h28b1_01": "1",  # avocado
        "h22a_01": "1",  # local cows
        "h14": "1",  # grid electricity
    }
    for variable, yes_code in variables.items():
        db.add(
            FieldMapping(
                survey_id=survey.id,
                variable_name=variable,
                platform_group="field_crops",
                yes_code=yes_code,
                yes_label="Yes",
                confirmed=True,
            )
        )
    db.commit()


@pytest.fixture
def food_items(db):
    items = [
        ("Mushroom", "h25_21", FoodGroup.FUNGI, None),
        ("Maize", "h24_01", FoodGroup.FIELD_CROP, None),
        ("Cassava", "h24_09", FoodGroup.FIELD_CROP, None),
        ("Amaranths", "h25_01", FoodGroup.VEGETABLE, None),
        ("Avocado", "h28b1_01", FoodGroup.FRUIT, "h28b2_01"),
        ("Local cows", "h22a_01", FoodGroup.LIVESTOCK, "h22b1_01"),
    ]
    for name, variable, group, count_variable in items:
        existing = db.scalar(select_food(db, variable))
        if existing is None:
            db.add(
                FoodItem(
                    standard_name=name,
                    group=group,
                    source_variable=variable,
                    count_variable=count_variable,
                )
            )
    db.commit()


def select_food(db, variable):
    from sqlalchemy import select

    return select(FoodItem).where(FoodItem.source_variable == variable)


def add_household(
    db,
    survey,
    hhid,
    *,
    weight,
    province="Kigali City",
    attributes=None,
    size=4,
    female_headed=False,
    education=None,
    phone=None,
    internet=None,
):
    household = Household(
        survey_id=survey.id,
        hhid=hhid,
        ml01=province,
        ml02="Gasabo",
        ml03="Sector A",
        ml07="Urban",
        hh_weight=Decimal(str(weight)),
        household_size=size,
        female_headed=female_headed,
        head_education_code=education,
        head_phone_type_code=phone,
        head_internet_code=internet,
        attributes=attributes or {},
    )
    db.add(household)
    return household


@pytest.fixture
def dictionary_entries(db, survey):
    """The questionnaire labels readiness resolution depends on."""
    from app.analysis.dictionary import DictionaryEntry, LabelValue, store_dictionary

    entries = [
        DictionaryEntry(
            "h05",
            "Roof material",
            "household",
            None,
            0,
            value_labels=(
                LabelValue("1", "Iron sheets"),
                LabelValue("2", "Tiles"),
                LabelValue("3", "Thatch"),
            ),
        ),
        DictionaryEntry(
            "h06",
            "Wall material",
            "household",
            None,
            1,
            value_labels=(
                LabelValue("1", "Bricks"),
                LabelValue("2", "Mud"),
                LabelValue("3", "Concrete"),
            ),
        ),
        DictionaryEntry(
            "h12",
            "Main drinking water source",
            "household",
            None,
            2,
            value_labels=(
                LabelValue("1", "Piped water"),
                LabelValue("2", "Protected spring"),
                LabelValue("3", "Lake"),
            ),
        ),
        DictionaryEntry(
            "h14",
            "Grid electricity connection",
            "household",
            None,
            3,
            value_labels=(LabelValue("1", "Yes"), LabelValue("2", "No")),
        ),
        DictionaryEntry(
            "p30a",
            "Highest level of education",
            "person",
            None,
            4,
            value_labels=(
                LabelValue("1", "None"),
                LabelValue("2", "Primary"),
                LabelValue("3", "Secondary"),
            ),
        ),
        DictionaryEntry(
            "p35",
            "Access to internet",
            "person",
            None,
            5,
            value_labels=(LabelValue("1", "Yes"), LabelValue("2", "No")),
        ),
        DictionaryEntry(
            "p36c",
            "Type of mobile phone",
            "person",
            None,
            6,
            value_labels=(
                LabelValue("1", "Smartphone"),
                LabelValue("2", "Feature phone"),
                LabelValue("3", "No phone"),
            ),
        ),
    ]
    store_dictionary(db, survey.id, entries)
    db.commit()


@pytest.fixture
def hand_data(db, survey, mapping_rows, food_items, dictionary_entries):
    """Six households with round weights so every share is easy to check.

    Mushroom growers: h2 (weight 100), h3 (weight 50).  Total weight 300.
    Weighted mushroom share  = 150 / 300 = 0.5
    Unweighted mushroom share = 2 / 6 = 0.3333
    """
    add_household(db, survey, "h1", weight=50, attributes={"h25_21": 2, "h24_01": 1})
    add_household(
        db,
        survey,
        "h2",
        weight=100,
        attributes={"h25_21": 1, "h24_01": 1, "h24_09": 1},
        female_headed=True,
        education="3",
        phone="1",
        internet="1",
    )
    add_household(db, survey, "h3", weight=50, attributes={"h25_21": 1, "h25_01": 1}, size=2)
    add_household(
        db,
        survey,
        "h4",
        weight=50,
        attributes={"h24_01": 2, "h28b1_01": 1, "h28b2_01": 10},
        education="2",
        phone="2",
    )
    add_household(
        db,
        survey,
        "h5",
        weight=25,
        attributes={"h22a_01": 1, "h22b1_01": 4},
        province="Northern",
        female_headed=True,
    )
    add_household(
        db,
        survey,
        "h6",
        weight=25,
        attributes={"h22a_01": 2},
        province="Northern",
        education="3",
        phone="1",
        internet="1",
    )
    db.commit()
    return survey


def test_weighted_and_unweighted_mushroom_shares_match_hand_arithmetic(db, hand_data):
    result = aggregation.run_aggregation(db, hand_data)
    assert result.foods_scored == 6
    assert result.mushroom_rows > 0

    row = db.execute(
        text(
            """
            SELECT households_total, weighted_households_total, mushroom_households,
                   weighted_mushroom_households
            FROM mushroom_summary
            WHERE survey_id = :survey_id AND level = 'national'
            """
        ),
        {"survey_id": str(hand_data.id)},
    ).one()

    assert row.households_total == 6
    assert float(row.weighted_households_total) == 300.0
    assert row.mushroom_households == 2
    assert float(row.weighted_mushroom_households) == 150.0
    assert float(row.weighted_mushroom_households) / float(row.weighted_households_total) == 0.5
    assert row.mushroom_households / row.households_total == pytest.approx(1 / 3, rel=1e-9)


def test_mushroom_breakdowns_are_computed_from_the_fixture(db, hand_data):
    aggregation.run_aggregation(db, hand_data)
    row = (
        db.execute(
            text(
                "SELECT * FROM mushroom_summary WHERE survey_id = :survey_id AND level = 'national'"
            ),
            {"survey_id": str(hand_data.id)},
        )
        .mappings()
        .one()
    )

    # Both mushroom households are urban in the fixture.
    assert row["urban_mushroom_households"] == 2
    assert row["rural_mushroom_households"] == 0
    # h2 is female headed out of two mushroom households.
    assert row["female_headed_mushroom_households"] == 1
    # Only h2 among mushroom growers has a grain, fruit or livestock activity.
    # h3 grows amaranths, which is a vegetable and not part of this measure.
    assert row["existing_activity_households"] == 1
    # Mushroom households: h2 size 4 and h3 size 2, average 3.
    assert float(row["mushroom_average_household_size"]) == 3.0

    province = db.execute(
        text(
            """
            SELECT province, mushroom_households FROM mushroom_summary
            WHERE survey_id = :survey_id AND level = 'province' ORDER BY province
            """
        ),
        {"survey_id": str(hand_data.id)},
    ).all()
    assert [(row.province, row.mushroom_households) for row in province] == [
        ("Kigali City", 2),
        ("Northern", 0),
    ]


def test_province_food_summary_weighted_share_matches_hand_arithmetic(db, hand_data):
    aggregation.run_aggregation(db, hand_data)
    rows = db.execute(
        text(
            """
            SELECT f.standard_name, p.households_total, p.households_with_food,
                   p.weighted_households_with_food, p.weighted_households_total
            FROM province_food_summary p
            JOIN food_items f ON f.id = p.food_item_id
            WHERE p.survey_id = :survey_id AND p.level = 'national'
            """
        ),
        {"survey_id": str(hand_data.id)},
    ).all()
    by_name = {row.standard_name: row for row in rows}

    # Maize: h1 and h2 (50 + 100) out of 300 total weight.
    assert by_name["Maize"].households_with_food == 2
    assert float(by_name["Maize"].weighted_households_with_food) == 150.0
    assert float(by_name["Maize"].weighted_households_total) == 300.0

    # Local cows: h5 only, weight 25.
    assert by_name["Local cows"].households_with_food == 1
    assert float(by_name["Local cows"].weighted_households_with_food) == 25.0

    # Avocado: h4 only, weight 50, with ten trees recorded.
    assert by_name["Avocado"].households_with_food == 1


def test_average_count_uses_only_households_that_have_the_item(db, hand_data):
    aggregation.run_aggregation(db, hand_data)
    row = db.execute(
        text(
            """
            SELECT p.average_count, p.average_count_basis
            FROM province_food_summary p
            JOIN food_items f ON f.id = p.food_item_id
            WHERE p.survey_id = :survey_id AND p.level = 'national'
              AND f.standard_name = 'Avocado'
            """
        ),
        {"survey_id": str(hand_data.id)},
    ).one()
    assert row.average_count_basis == 1
    assert float(row.average_count) == 10.0


def test_hierarchy_has_one_row_per_geography_level(db, hand_data):
    aggregation.run_aggregation(db, hand_data)
    levels = db.execute(
        text(
            """
            SELECT level, count(*) FROM province_food_summary
            WHERE survey_id = :survey_id AND food_item_id = (
                SELECT id FROM food_items WHERE source_variable = 'h25_21'
            )
            GROUP BY level
            """
        ),
        {"survey_id": str(hand_data.id)},
    ).all()
    # national, two provinces, and districts and sectors share the fixture names.
    counts = {row.level: row.count for row in levels}
    assert counts["national"] == 1
    assert counts["province"] == 2


def test_diversity_counts_distinct_food_items(db, hand_data):
    aggregation.run_aggregation(db, hand_data)
    values = db.execute(
        text(
            """
            SELECT hhid, food_diversity_count FROM households
            WHERE survey_id = :survey_id ORDER BY hhid
            """
        ),
        {"survey_id": str(hand_data.id)},
    ).all()
    by_hhid = {row.hhid: row.food_diversity_count for row in values}
    assert by_hhid["h1"] == 1  # maize
    assert by_hhid["h2"] == 2  # maize, cassava
    assert by_hhid["h3"] == 1  # amaranths
    assert by_hhid["h4"] == 1  # avocado
    assert by_hhid["h5"] == 0  # livestock is not part of the food diversity count
    assert by_hhid["h6"] == 0

    summary = db.execute(
        text(
            """
            SELECT households_total, average_diversity, distribution
            FROM household_diversity_summary
            WHERE survey_id = :survey_id AND level = 'national'
            """
        ),
        {"survey_id": str(hand_data.id)},
    ).one()
    assert summary.households_total == 6
    # Values are 1, 2, 1, 1, 0, 0 => mean 5/6, stored with four decimal places.
    assert float(summary.average_diversity) == pytest.approx(round(5 / 6, 4), abs=1e-4)
    assert summary.distribution["1"] == 3
    assert summary.distribution["0"] == 2


def test_readiness_score_is_weighted_from_its_components(db, hand_data):
    aggregation.run_aggregation(db, hand_data)
    row = (
        db.execute(
            text(
                """
            SELECT score, components, weights
            FROM readiness_summary
            WHERE survey_id = :survey_id AND level = 'national'
            """
            ),
            {"survey_id": str(hand_data.id)},
        )
        .mappings()
        .one()
    )

    components = row["components"]
    # No household records h14 = 1, so nobody has grid electricity.
    assert components["electricity"]["households"] == 0
    # Education codes 3 and 2 appear; only code 3 counts as secondary or above.
    assert components["education"]["households"] == 2
    # Smartphone and feature phone both count, so h2, h4 and h6 qualify.
    assert components["phone"]["households"] == 3
    assert row["score"] is not None
    assert 0 <= float(row["score"]) <= 100
    assert set(row["weights"]) >= {"shelter", "water", "electricity", "household_size"}


def test_aggregation_replaces_previous_results(db, hand_data):
    aggregation.run_aggregation(db, hand_data)
    first = db.scalar(
        text("SELECT count(*) FROM province_food_summary WHERE survey_id = :survey_id"),
        {"survey_id": str(hand_data.id)},
    )
    aggregation.run_aggregation(db, hand_data)
    second = db.scalar(
        text("SELECT count(*) FROM province_food_summary WHERE survey_id = :survey_id"),
        {"survey_id": str(hand_data.id)},
    )
    assert first == second


def test_food_items_without_a_confirmed_yes_code_are_skipped(db, survey):
    db.add(
        FoodItem(standard_name="Unmapped food", group=FoodGroup.FRUIT, source_variable="h28b1_09")
    )
    db.commit()
    result = aggregation.run_aggregation(db, survey)
    assert result.foods_scored == 0
    assert any("confirmed yes code" in warning for warning in result.warnings)


def test_missing_mushroom_mapping_is_reported(db, survey, food_items):
    result = aggregation.run_aggregation(db, survey)
    assert any("Mushroom is not mapped" in warning for warning in result.warnings)


class TestSuppression:
    def test_cell_at_or_above_threshold_is_released(self):
        value = cell(0.25, 10, 10)
        assert value.suppressed is False
        assert value.as_dict()["value"] == 0.25

    def test_cell_below_threshold_is_withheld(self):
        value = cell(0.25, 9, 10)
        assert value.suppressed is True
        payload = value.as_dict()
        assert payload["value"] is None
        assert payload["count"] is None
        assert payload["translation_key"] == "analysis.fewer_than_threshold"

    def test_threshold_is_configurable(self):
        assert cell(0.1, 5, 5).suppressed is False
        assert cell(0.1, 5, 6).suppressed is True

    def test_missing_count_is_always_suppressed(self):
        assert cell(0.1, None, 1).suppressed is True


def test_geo_level_ordering_of_rows(db, hand_data):
    aggregation.run_aggregation(db, hand_data)
    levels = {
        row[0]
        for row in db.execute(
            text("SELECT DISTINCT level FROM readiness_summary WHERE survey_id = :survey_id"),
            {"survey_id": str(hand_data.id)},
        ).all()
    }
    assert levels <= {level.value for level in GeoLevel}
    assert GeoLevel.NATIONAL.value in levels


def test_unique_survey_ids_are_used(db, hand_data):
    other = Survey(
        name="Other",
        year=2022,
        original_filename="o.dta",
        stored_path="o/o.dta",
        file_size_bytes=1,
        checksum_sha256=uuid.uuid4().hex * 2,
        status="ready",
    )
    db.add(other)
    db.commit()
    aggregation.run_aggregation(db, hand_data)
    aggregation.run_aggregation(db, other)
    hand_rows = db.scalar(
        text("SELECT count(*) FROM province_food_summary WHERE survey_id = :survey_id"),
        {"survey_id": str(hand_data.id)},
    )
    other_rows = db.scalar(
        text("SELECT count(*) FROM province_food_summary WHERE survey_id = :survey_id"),
        {"survey_id": str(other.id)},
    )
    assert hand_rows > 0
    assert other_rows == 0


def test_readiness_supports_configured_codes_when_the_file_has_no_labels(
    db, survey, mapping_rows, food_items
):
    """Person variables often ship without value labels, so settings may declare codes."""
    from app.analysis.dictionary import DictionaryEntry, store_dictionary
    from app.services import settings_service

    store_dictionary(
        db,
        survey.id,
        [
            DictionaryEntry("h14", "Grid electricity", "household", None, 0),
        ],
    )
    db.commit()

    definitions = aggregation.readiness_definitions(db)
    definitions["electricity"] = {
        "variable": "h14",
        "label_keywords": ["yes"],
        "codes": ["1"],
    }
    settings_service.set_value(db, "readiness.definitions", definitions)

    add_household(db, survey, "c1", weight=10, attributes={"h14": 1})
    add_household(db, survey, "c2", weight=10, attributes={"h14": 2})
    db.commit()

    aggregation.run_aggregation(db, survey)
    row = db.execute(
        text(
            """
            SELECT components FROM readiness_summary
            WHERE survey_id = :survey_id AND level = 'national'
            """
        ),
        {"survey_id": str(survey.id)},
    ).scalar()
    assert row["electricity"]["households"] == 1
    assert row["electricity"]["definition_source"] == "configured_codes"


def test_household_size_component_is_not_suppressed_by_a_missing_count(db, hand_data):
    aggregation.run_aggregation(db, hand_data)
    row = db.execute(
        text(
            """
            SELECT components FROM readiness_summary
            WHERE survey_id = :survey_id AND level = 'national'
            """
        ),
        {"survey_id": str(hand_data.id)},
    ).scalar()
    assert row["household_size"]["households"] == 6
    assert row["household_size"]["weighted_share"] is not None
