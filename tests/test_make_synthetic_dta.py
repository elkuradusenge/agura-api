"""The synthetic .dta generator must look like the real questionnaire."""

from __future__ import annotations

import pandas as pd
import pyreadstat

from scripts.make_synthetic_dta import build_frame, write_synthetic_dta


def test_generated_file_has_labels_and_value_labels(synthetic_dta):
    _, meta = pyreadstat.read_dta(synthetic_dta, metadataonly=True)
    labels = dict(zip(meta.column_names, meta.column_labels, strict=False))
    assert labels["ml01"] == "Province"
    assert labels["h25_21"] == "Grew mushroom"
    assert meta.variable_value_labels["h25_21"] == {1: "Yes", 2: "No"}
    assert meta.variable_value_labels["ml01"][1] == "Kigali City"
    assert meta.number_rows > 0


def test_generated_file_is_person_level_with_repeated_household_variables(synthetic_dta):
    frame, _ = pyreadstat.read_dta(synthetic_dta)
    assert "p01" in frame.columns
    assert "h25_21" in frame.columns
    assert frame["hhid"].duplicated().any(), "household rows must repeat per person"


def test_generated_file_contains_injected_problems():
    frame = build_frame(person_rows=600, seed=3)
    assert frame.duplicated().any(), "exact duplicate rows are expected"
    assert (frame["p04"] < 0).any(), "invalid ages below zero are expected"
    assert (frame["p04"] > 120).any(), "invalid ages above 120 are expected"
    assert frame["ml02"].astype("string").str.strip().eq("").any(), "blank geography expected"
    assert (frame["ml02"] != frame["ml02"].str.strip()).any(), "untrimmed geography expected"


def test_generator_is_deterministic(tmp_path):
    first = write_synthetic_dta(tmp_path / "a.dta", person_rows=300, seed=11)
    second = write_synthetic_dta(tmp_path / "b.dta", person_rows=300, seed=11)
    frame_a, _ = pyreadstat.read_dta(str(first))
    frame_b, _ = pyreadstat.read_dta(str(second))
    pd.testing.assert_frame_equal(frame_a, frame_b)
