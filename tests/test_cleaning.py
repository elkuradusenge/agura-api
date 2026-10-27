"""Cleaning rules: trimming, missing codes, invalid ages, geography and duplicates."""

from __future__ import annotations

import pandas as pd

from app.analysis.cleaning import normalise_chunk, standardise_geography
from app.analysis.dictionary import DictionaryEntry, LabelValue


def make_dictionary(**overrides) -> dict[str, DictionaryEntry]:
    base = {
        "hhid": DictionaryEntry("hhid", "Household ID", "identifier", "str", 0),
        "ml01": DictionaryEntry(
            "ml01",
            "Province",
            "geography",
            "double",
            1,
            value_labels=(
                LabelValue("1", "Kigali City"),
                LabelValue("2", "Northern"),
            ),
        ),
        "ml02": DictionaryEntry("ml02", "District", "geography", "str", 2),
        "ml03": DictionaryEntry("ml03", "Sector", "geography", "str", 3),
        "ml07": DictionaryEntry(
            "ml07",
            "Area of residence",
            "geography",
            "double",
            4,
            value_labels=(LabelValue("1", "Urban"), LabelValue("2", "Rural")),
        ),
        "p01": DictionaryEntry("p01", "Person ID", "person", "double", 5),
        "p03": DictionaryEntry("p03", "Sex", "person", "double", 6),
        "p04": DictionaryEntry(
            "p04",
            "Age",
            "person",
            "double",
            7,
            value_labels=(LabelValue("99", "Missing", is_missing=True),),
        ),
        "h05": DictionaryEntry("h05", "Roof", "household", "double", 8),
        "h08": DictionaryEntry(
            "h08",
            "Rooms",
            "household",
            "double",
            9,
            value_labels=(LabelValue("999", "Missing", is_missing=True),),
        ),
    }
    base.update(overrides)
    return base


def test_province_codes_are_decoded_and_names_standardised():
    dictionary = make_dictionary()
    frame = pd.DataFrame(
        {
            "hhid": ["1", "2", "3"],
            "ml01": [1.0, 2.0, 1.0],
            "ml02": ["  gasabo ", "KICUKIRO", "Nyarugenge"],
            "ml03": ["sector a", "Sector B", "  Sector C"],
            "ml07": [1.0, 2.0, 1.0],
        }
    )
    cleaned, stats = normalise_chunk(frame, dictionary)
    assert list(cleaned["ml01"]) == ["Kigali City", "Northern", "Kigali City"]
    assert list(cleaned["ml02"]) == ["Gasabo", "Kicukiro", "Nyarugenge"]
    assert list(cleaned["ml03"]) == ["Sector A", "Sector B", "Sector C"]
    assert list(cleaned["ml07"]) == ["Urban", "Rural", "Urban"]
    assert stats.geography_standardised > 0


def test_province_alias_table_handles_local_names():
    series = pd.Series(["umujyi wa kigali", "AMAJYARUGURU", "iburengerazuba"])
    assert list(standardise_geography(series, "ml01")) == ["Kigali City", "Northern", "Western"]


def test_declared_missing_codes_become_null():
    dictionary = make_dictionary()
    frame = pd.DataFrame({"hhid": ["1", "2"], "h08": [999.0, 3.0], "p04": [99.0, 30.0]})
    cleaned, stats = normalise_chunk(frame, dictionary)
    assert pd.isna(cleaned.loc[0, "h08"])
    assert cleaned.loc[1, "h08"] == 3.0
    assert pd.isna(cleaned.loc[0, "p04"])
    assert stats.missing_codes_to_null == 2


def test_blank_text_becomes_null():
    dictionary = make_dictionary()
    frame = pd.DataFrame({"hhid": ["1", "2"], "ml02": ["   ", "Huye"]})
    cleaned, stats = normalise_chunk(frame, dictionary)
    assert pd.isna(cleaned.loc[0, "ml02"])
    assert cleaned.loc[1, "ml02"] == "Huye"
    assert stats.blanks_to_null == 1


def test_impossible_ages_become_null_and_are_counted():
    dictionary = make_dictionary()
    frame = pd.DataFrame({"hhid": ["1", "2", "3", "4"], "p04": [150.0, -3.0, 30.0, 0.0]})
    cleaned, stats = normalise_chunk(frame, dictionary)
    assert pd.isna(cleaned.loc[0, "p04"])
    assert pd.isna(cleaned.loc[1, "p04"])
    assert cleaned.loc[2, "p04"] == 30
    assert cleaned.loc[3, "p04"] == 0
    assert stats.invalid_ages == 2


def test_columns_outside_the_dictionary_are_dropped():
    dictionary = make_dictionary()
    frame = pd.DataFrame({"hhid": ["1"], "mystery_column": [5]})
    cleaned, _ = normalise_chunk(frame, dictionary)
    assert list(cleaned.columns) == ["hhid"]


def test_column_names_are_lowercased():
    dictionary = make_dictionary()
    frame = pd.DataFrame({"HHID": ["1"], "ML02": [" Huye "]})
    cleaned, _ = normalise_chunk(frame, dictionary)
    assert "hhid" in cleaned.columns
    assert cleaned.loc[0, "ml02"] == "Huye"


def test_null_counts_are_reported():
    dictionary = make_dictionary()
    frame = pd.DataFrame({"hhid": ["1", "2", "3"], "ml02": ["A", None, None]})
    _, stats = normalise_chunk(frame, dictionary)
    assert stats.null_counts["ml02"] == 2


def test_census_province_labels_are_canonicalised():
    series = pd.Series(
        ["City of Kigali", "Southern Province", "WESTERN PROVINCE", "northern province"]
    )
    assert list(standardise_geography(series, "ml01")) == [
        "Kigali City",
        "Southern",
        "Western",
        "Northern",
    ]


def test_title_case_keeps_small_words_lowercase():
    from app.analysis.cleaning import title_case_name

    assert title_case_name("NYARUGENGE") == "Nyarugenge"
    assert title_case_name("city of kigali") == "City of Kigali"
    assert title_case_name("  gicumbi ") == "Gicumbi"


def test_whole_number_codes_are_stored_as_json_integers():
    import json

    dictionary = make_dictionary(
        h25_21=DictionaryEntry("h25_21", "Grew mushroom", "household", "double", 10)
    )
    frame = pd.DataFrame({"hhid": ["1", "2"], "h25_21": [1.0, 2.0]})
    from app.analysis.cleaning import build_rows

    cleaned, _ = normalise_chunk(frame, dictionary)
    household_rows, _ = build_rows(cleaned)
    assert json.loads(household_rows[0][-1]) == {"h25_21": 1}
    assert json.loads(household_rows[1][-1]) == {"h25_21": 2}
