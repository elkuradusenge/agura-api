"""Dictionary building, section detection and yes code detection."""

from __future__ import annotations

import pytest

from app.analysis.constants import platform_group_for, section_for
from app.analysis.dictionary import (
    build_dictionary,
    classify_label,
    load_dictionary,
    read_metadata,
    store_dictionary,
    value_to_text,
)
from app.models.enums import PlatformGroup


@pytest.fixture
def entries(synthetic_dta):
    return build_dictionary(read_metadata(synthetic_dta))


def test_dictionary_keeps_labels_and_value_labels(entries):
    by_name = {entry.variable_name: entry for entry in entries}
    mushroom = by_name["h25_21"]
    assert mushroom.label == "Grew mushroom"
    assert mushroom.yes_code == "1"
    assert mushroom.yes_label == "Yes"
    assert mushroom.has_value_labels
    assert by_name["ml01"].yes_code is None


def test_detected_yes_codes_come_from_labels_not_assumptions(entries):
    by_name = {entry.variable_name: entry for entry in entries}
    for variable in ("h25_21", "h24_01", "h22a_01", "h21_01"):
        assert by_name[variable].yes_code == "1"


def test_missing_codes_are_taken_from_value_labels(entries):
    by_name = {entry.variable_name: entry for entry in entries}
    assert by_name["h08"].missing_codes == ("999",)


def test_sections_and_platform_groups(entries):
    by_name = {entry.variable_name: entry for entry in entries}
    assert by_name["ml01"].section == "geography"
    assert by_name["p04"].section == "person"
    assert by_name["h05"].section == "household"
    assert platform_group_for("h25_21") is PlatformGroup.VEGETABLES
    assert platform_group_for("h24_01") is PlatformGroup.FIELD_CROPS
    assert platform_group_for("h22a_01") is PlatformGroup.LIVESTOCK
    assert platform_group_for("h21_01") is PlatformGroup.ASSETS
    assert platform_group_for("ml07") is PlatformGroup.GEOGRAPHY
    assert platform_group_for("hh_weight") is PlatformGroup.WEIGHTS
    assert section_for("pop_weight") == "weights"


def test_dictionary_round_trip_through_the_database(db, entries):
    import uuid

    survey_id = uuid.uuid4()
    from app.models.survey import Survey

    db.add(
        Survey(
            id=survey_id,
            name="Round trip",
            year=2022,
            original_filename="a.dta",
            stored_path="a/a.dta",
            file_size_bytes=10,
            checksum_sha256="0" * 64,
        )
    )
    db.commit()

    stored = store_dictionary(db, survey_id, entries)
    db.commit()
    assert stored == len(entries)

    loaded = load_dictionary(db, survey_id)
    assert set(loaded) == {entry.variable_name for entry in entries}
    assert loaded["h25_21"].yes_code == "1"
    assert loaded["h08"].missing_codes == ("999",)


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Yes", "yes"),
        ("Grown", "yes"),
        ("Reared", "yes"),
        ("No", "no"),
        ("Missing", "missing"),
        ("Not stated", "missing"),
        ("Catholic", None),
    ],
)
def test_classify_label(label, expected):
    assert classify_label(label) == expected


def test_value_to_text_keeps_integer_codes_clean():
    assert value_to_text(1.0) == "1"
    assert value_to_text(2.5) == "2.5"
    assert value_to_text("3") == "3"
