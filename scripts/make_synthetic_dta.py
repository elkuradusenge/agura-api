"""Generate a small synthetic census .dta so the pipeline can run without real data.

The layout matches the real file: person level rows with the household variables
repeated on every row, variable labels and value labels, injected duplicates,
impossible ages, inconsistent household variables and untidy geography names.

    python -m scripts.make_synthetic_dta --out /tmp/synthetic_census.dta

No real microdata is ever derived from this script and nothing here is committed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyreadstat

MUSHROOM_VARIABLE = "h25_21"

PROVINCES = [
    (1, "Kigali City", ["Gasabo", "Kicukiro", "Nyarugenge"]),
    (2, "Northern", ["Burera", "Gakenke", "Gicumbi", "Musanze", "Rulindo"]),
    (3, "Southern", ["Gisagara", "Huye", "Kamonyi", "Muhanga", "Nyamagabe"]),
    (4, "Eastern", ["Bugesera", "Gatsibo", "Kayonza", "Kirehe", "Ngoma"]),
    (5, "Western", ["Karongi", "Ngororero", "Nyabihu", "Rubavu", "Rusizi"]),
]

SECTORS = ["Sector A", "Sector B", "Sector C", "Sector D", "Sector E"]

YES_NO = {1.0: "Yes", 2.0: "No"}

COLUMN_LABELS = {
    "ml01": "Province",
    "ml02": "District",
    "ml03": "Sector",
    "ml07": "Area of residence",
    "hhid": "Household ID",
    "p01": "Person ID",
    "p02": "Relationship to household head",
    "p03": "Sex",
    "p04": "Age",
    "p06": "Marital status",
    "p13": "Religious affiliation",
    "p29": "Ever or currently attending school",
    "p30a": "Highest level of education",
    "p34": "Internet use",
    "p36c": "Type of mobile phone",
    "p46": "Institutional sector",
    "p49": "Status in employment",
    "h01": "Type of habitat",
    "h05": "Roof material",
    "h06": "Wall material",
    "h07": "Floor material",
    "h08": "Number of rooms",
    "h11": "Main water source general use",
    "h12": "Main drinking water source",
    "h14": "Grid electricity connection",
    "h15": "Lighting energy",
    "h16": "Cooking energy",
    "h21_01": "Owns a radio",
    "h21_02": "Owns a television",
    "h22": "Owns livestock or beehive",
    "h22a_01": "Reared local cows",
    "h22a_02": "Reared exotic cows",
    "h22b1_01": "Number of local cows",
    "hh_weight": "Household weight",
    "pop_weight": "Population weight",
    "h23": "Household grew crops in the last 12 months",
    "h24_01": "Grew maize",
    "h24_02": "Grew rice",
    "h24_09": "Grew cassava",
    "h25_01": "Grew amaranths",
    "h25_02": "Grew tomato",
    "h25_21": "Grew mushroom",
    "h28a": "Any fruit tree",
    "h28b1_01": "Grew avocado",
    "h28b1_06": "Grew mango",
    "h28b2_01": "Number of avocado trees",
}

VALUE_LABELS = {
    "ml01": {1.0: "Kigali City", 2.0: "Northern", 3.0: "Southern", 4.0: "Eastern", 5.0: "Western"},
    "ml07": {1.0: "Urban", 2.0: "Rural"},
    "p02": {1.0: "Head", 2.0: "Spouse", 3.0: "Child", 4.0: "Other relative"},
    "p03": {1.0: "Male", 2.0: "Female"},
    "p06": {1.0: "Never married", 2.0: "Married", 3.0: "Widowed", 4.0: "Divorced"},
    "p13": {1.0: "Catholic", 2.0: "Protestant", 3.0: "Muslim", 4.0: "Other"},
    "p29": {1.0: "Yes", 2.0: "No"},
    "p30a": {1.0: "None", 2.0: "Primary", 3.0: "Secondary", 4.0: "University"},
    "p34": {1.0: "Yes", 2.0: "No"},
    "p36c": {1.0: "Smartphone", 2.0: "Feature phone", 3.0: "No phone"},
    "p46": {1.0: "Public", 2.0: "Private", 3.0: "Household"},
    "p49": {1.0: "Employee", 2.0: "Self employed", 3.0: "Unpaid family worker"},
    "h01": {1.0: "Umudugudu house", 2.0: "Apartment", 3.0: "Other"},
    "h05": {1.0: "Iron sheets", 2.0: "Tiles", 3.0: "Thatch", 4.0: "Concrete"},
    "h06": {1.0: "Bricks", 2.0: "Mud", 3.0: "Wood", 4.0: "Concrete"},
    "h07": {1.0: "Cement", 2.0: "Earth", 3.0: "Tiles"},
    "h08": {999.0: "Missing"},
    "h11": {1.0: "Piped water", 2.0: "Protected spring", 3.0: "Unprotected spring", 4.0: "Lake"},
    "h12": {1.0: "Piped water", 2.0: "Protected spring", 3.0: "Unprotected spring", 4.0: "Lake"},
    "h14": {1.0: "Yes", 2.0: "No"},
    "h15": {1.0: "Electricity", 2.0: "Solar", 3.0: "Kerosene", 4.0: "Firewood"},
    "h16": {1.0: "Firewood", 2.0: "Charcoal", 3.0: "Gas", 4.0: "Electricity"},
    "h21_01": YES_NO,
    "h21_02": YES_NO,
    "h22": YES_NO,
    "h22a_01": YES_NO,
    "h22a_02": YES_NO,
    "h23": YES_NO,
    "h24_01": YES_NO,
    "h24_02": YES_NO,
    "h24_09": YES_NO,
    "h25_01": YES_NO,
    "h25_02": YES_NO,
    "h25_21": YES_NO,
    "h28a": YES_NO,
    "h28b1_01": YES_NO,
    "h28b1_06": YES_NO,
}

FLAG_VARIABLES = [
    "h21_01",
    "h21_02",
    "h22",
    "h22a_01",
    "h22a_02",
    "h23",
    "h24_01",
    "h24_02",
    "h24_09",
    "h25_01",
    "h25_02",
    "h25_21",
    "h28a",
    "h28b1_01",
    "h28b1_06",
]


def build_frame(person_rows: int = 3000, seed: int = 7) -> pd.DataFrame:
    """Create a person level frame with repeated household variables."""
    rng = np.random.default_rng(seed)
    households = max(person_rows // 3, 10)
    records: list[dict] = []

    for household_index in range(households):
        province_code, province_name, districts = PROVINCES[household_index % len(PROVINCES)]
        district = districts[household_index % len(districts)]
        sector = SECTORS[household_index % len(SECTORS)]
        area = 1.0 if province_code == 1 and household_index % 2 == 0 else 2.0
        hhid = f"{province_code}{household_index:06d}"

        mushroom_share = 0.05 if province_code == 1 else 0.015
        grew_crops = 1.0 if rng.random() < 0.85 else 2.0
        hh_weight = float(round(rng.uniform(20, 400), 4))
        person_count = int(rng.integers(1, 7))

        household = {
            "ml01": float(province_code),
            "ml02": district,
            "ml03": sector,
            "ml07": area,
            "hhid": hhid,
            "hh_weight": hh_weight,
            "h01": float(rng.integers(1, 4)),
            "h05": float(rng.integers(1, 5)),
            "h06": float(rng.integers(1, 5)),
            "h07": float(rng.integers(1, 4)),
            "h08": (999.0 if rng.random() < 0.03 else float(rng.integers(0, 8))),
            "h11": float(rng.integers(1, 5)),
            "h12": float(rng.integers(1, 5)),
            "h14": 1.0 if rng.random() < 0.4 else 2.0,
            "h15": float(rng.integers(1, 5)),
            "h16": float(rng.integers(1, 5)),
            "h21_01": 1.0 if rng.random() < 0.6 else 2.0,
            "h21_02": 1.0 if rng.random() < 0.25 else 2.0,
            "h22": 1.0 if rng.random() < 0.55 else 2.0,
            "h22a_01": 1.0 if rng.random() < 0.35 else 2.0,
            "h22a_02": 1.0 if rng.random() < 0.2 else 2.0,
            "h22b1_01": float(rng.integers(0, 6)),
            "h23": grew_crops,
            "h24_01": 1.0 if grew_crops == 1.0 and rng.random() < 0.7 else 2.0,
            "h24_02": 1.0 if grew_crops == 1.0 and rng.random() < 0.15 else 2.0,
            "h24_09": 1.0 if grew_crops == 1.0 and rng.random() < 0.5 else 2.0,
            "h25_01": 1.0 if grew_crops == 1.0 and rng.random() < 0.6 else 2.0,
            "h25_02": 1.0 if grew_crops == 1.0 and rng.random() < 0.3 else 2.0,
            "h25_21": 1.0 if rng.random() < mushroom_share else 2.0,
            "h28a": 1.0 if grew_crops == 1.0 and rng.random() < 0.45 else 2.0,
            "h28b1_01": 1.0 if rng.random() < 0.3 else 2.0,
            "h28b1_06": 1.0 if rng.random() < 0.2 else 2.0,
            "h28b2_01": float(rng.integers(0, 9)),
        }

        for person_index in range(1, person_count + 1):
            age = int(rng.integers(0, 95))
            roll = rng.random()
            if roll < 0.02:
                age = 150
            elif roll < 0.03:
                age = -3
            record = dict(household)
            record.update(
                {
                    "p01": float(person_index),
                    "p02": 1.0 if person_index == 1 else float(rng.integers(2, 5)),
                    "p03": 1.0 if rng.random() < 0.48 else 2.0,
                    "p04": float(age),
                    "p06": float(rng.integers(1, 5)),
                    "p13": float(rng.integers(1, 5)),
                    "p29": 1.0 if rng.random() < 0.7 else 2.0,
                    "p30a": float(rng.integers(1, 5)),
                    "p34": 1.0 if rng.random() < 0.35 else 2.0,
                    "p36c": float(rng.integers(1, 4)),
                    "p46": float(rng.integers(1, 4)),
                    "p49": float(rng.integers(1, 4)),
                    "pop_weight": float(round(rng.uniform(20, 400), 4)),
                }
            )
            records.append(record)

    frame = pd.DataFrame.from_records(records)

    # Untidy geography strings that cleaning has to standardise.
    frame.loc[frame.index[:5], "ml02"] = " " + frame.loc[frame.index[:5], "ml02"] + "  "
    frame.loc[frame.index[5:8], "ml03"] = frame.loc[frame.index[5:8], "ml03"].str.lower()

    # Exact duplicate rows.
    duplicates = frame.iloc[:4].copy()
    # Repeated household and person rows with the same values.
    repeated = frame.iloc[4:8].copy()
    # A household whose repeated rows disagree on a household variable.
    conflict = frame.iloc[8:10].copy()
    conflict.loc[:, "h05"] = 1.0 if float(conflict.iloc[0]["h05"]) != 1.0 else 2.0
    # A blank district that must become NULL.
    blank = frame.iloc[10:11].copy()
    blank.loc[:, "ml02"] = ""

    frame = pd.concat([frame, duplicates, repeated, conflict, blank], ignore_index=True)
    return frame


def write_synthetic_dta(path: str | Path, person_rows: int = 3000, seed: int = 7) -> Path:
    frame = build_frame(person_rows=person_rows, seed=seed)
    columns = [column for column in frame.columns if column in COLUMN_LABELS]
    frame = frame[columns]
    column_labels = [COLUMN_LABELS[column] for column in columns]
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    pyreadstat.write_dta(
        frame,
        str(destination),
        file_label="AGUra synthetic census",
        column_labels=column_labels,
        variable_value_labels={
            name: labels for name, labels in VALUE_LABELS.items() if name in columns
        },
    )
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a synthetic census .dta file")
    parser.add_argument("--out", default="synthetic_census.dta")
    parser.add_argument("--rows", type=int, default=3000)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args(argv)

    path = write_synthetic_dta(args.out, person_rows=args.rows, seed=args.seed)
    print(f"Synthetic census written to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
