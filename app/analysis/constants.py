"""Vocabulary shared by the pipeline stages."""

from __future__ import annotations

import re

from app.models.enums import FoodGroup, PlatformGroup

YES_LABELS = {
    "yes",
    "grown",
    "grew",
    "reared",
    "own",
    "owns",
    "owned",
    "has",
    "have",
    "available",
    "true",
}

NO_LABELS = {"no", "not grown", "not reared", "none", "false"}

MISSING_LABELS = {
    "missing",
    "not stated",
    "unknown",
    "blank",
    "no response",
    "not applicable",
    "not reported",
    "none given",
    "declined",
}

GEOGRAPHY_VARIABLES = ("ml01", "ml02", "ml03", "ml07")
IDENTIFIER_VARIABLES = ("hhid", "p01")
WEIGHT_VARIABLES = ("hh_weight", "pop_weight")

EXPLICIT_GROUPS: dict[str, PlatformGroup] = {
    "ml01": PlatformGroup.GEOGRAPHY,
    "ml02": PlatformGroup.GEOGRAPHY,
    "ml03": PlatformGroup.GEOGRAPHY,
    "ml07": PlatformGroup.GEOGRAPHY,
    "hhid": PlatformGroup.PERSON_IDENTIFIERS,
    "p01": PlatformGroup.PERSON_IDENTIFIERS,
    "hh_weight": PlatformGroup.WEIGHTS,
    "pop_weight": PlatformGroup.WEIGHTS,
    "p03": PlatformGroup.PERSON_DEMOGRAPHICS,
    "p04": PlatformGroup.PERSON_DEMOGRAPHICS,
    "p06": PlatformGroup.PERSON_DEMOGRAPHICS,
    "p13": PlatformGroup.PERSON_DEMOGRAPHICS,
    "p29": PlatformGroup.EDUCATION_DIGITAL,
    "p30a": PlatformGroup.EDUCATION_DIGITAL,
    "p30b": PlatformGroup.EDUCATION_DIGITAL,
    "p31": PlatformGroup.EDUCATION_DIGITAL,
    "p32": PlatformGroup.EDUCATION_DIGITAL,
    "p33": PlatformGroup.EDUCATION_DIGITAL,
    "p34": PlatformGroup.EDUCATION_DIGITAL,
    "p35": PlatformGroup.EDUCATION_DIGITAL,
    "p36c": PlatformGroup.EDUCATION_DIGITAL,
    "p46": PlatformGroup.EMPLOYMENT,
    "p47a": PlatformGroup.EMPLOYMENT,
    "p48a": PlatformGroup.EMPLOYMENT,
    "p49": PlatformGroup.EMPLOYMENT,
    "h22": PlatformGroup.LIVESTOCK,
    "h26": PlatformGroup.TEA_COFFEE,
    "h27": PlatformGroup.TEA_COFFEE,
    "h28a": PlatformGroup.FRUITS,
}

_PATTERN_GROUPS: tuple[tuple[re.Pattern[str], PlatformGroup], ...] = (
    (re.compile(r"^ml0[1-7]$"), PlatformGroup.GEOGRAPHY),
    (re.compile(r"^h(0[1-9]|1[0-9]|20)$"), PlatformGroup.HOUSING_SERVICES),
    (re.compile(r"^h21_\d{2}$"), PlatformGroup.ASSETS),
    (re.compile(r"^h22(a|b1|b2)_\d{2}$"), PlatformGroup.LIVESTOCK),
    (re.compile(r"^h23[a-z]?$"), PlatformGroup.FIELD_CROPS),
    (re.compile(r"^h24_\d{2}$"), PlatformGroup.FIELD_CROPS),
    (re.compile(r"^h25_\d{2}$"), PlatformGroup.VEGETABLES),
    (re.compile(r"^h28b[12]_\d{2}$"), PlatformGroup.FRUITS),
)

SECTION_PREFIXES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^ml\d"), "geography"),
    (re.compile(r"^hhid$"), "identifier"),
    (re.compile(r"^_?p\d"), "person"),
    (re.compile(r"^h\d"), "household"),
    (re.compile(r"weight$", re.IGNORECASE), "weights"),
)

HOUSEHOLD_SECTIONS = {"geography", "household", "weights", "identifier"}
PERSON_SECTIONS = {"person"}

FOOD_VARIABLES: tuple[tuple[str, str, FoodGroup, str | None], ...] = (
    ("h24_01", "Maize", FoodGroup.FIELD_CROP, None),
    ("h24_02", "Rice", FoodGroup.FIELD_CROP, None),
    ("h24_03", "Sorghum", FoodGroup.FIELD_CROP, None),
    ("h24_04", "Wheat", FoodGroup.FIELD_CROP, None),
    ("h24_05", "Bean", FoodGroup.FIELD_CROP, None),
    ("h24_06", "Pea", FoodGroup.FIELD_CROP, None),
    ("h24_07", "Groundnut", FoodGroup.FIELD_CROP, None),
    ("h24_08", "Soybean", FoodGroup.FIELD_CROP, None),
    ("h24_09", "Cassava", FoodGroup.FIELD_CROP, None),
    ("h24_10", "Sweet potato", FoodGroup.FIELD_CROP, None),
    ("h24_11", "Irish potato", FoodGroup.FIELD_CROP, None),
    ("h24_12", "Yams and taro", FoodGroup.FIELD_CROP, None),
    ("h24_13", "Cooking banana", FoodGroup.FIELD_CROP, None),
    ("h24_14", "Dessert banana", FoodGroup.FIELD_CROP, None),
    ("h24_15", "Banana for beer", FoodGroup.FIELD_CROP, None),
    ("h24_16", "Vegetables", FoodGroup.FIELD_CROP, None),
    ("h24_17", "Tea", FoodGroup.FIELD_CROP, None),
    ("h24_18", "Coffee", FoodGroup.FIELD_CROP, None),
    ("h24_19", "Sugarcane", FoodGroup.FIELD_CROP, None),
    ("h24_20", "Pyrethrum", FoodGroup.FIELD_CROP, None),
    ("h24_21", "Flowers", FoodGroup.FIELD_CROP, None),
    ("h24_22", "Other field crop", FoodGroup.FIELD_CROP, None),
    ("h25_01", "Amaranths", FoodGroup.VEGETABLE, None),
    ("h25_02", "Tomato", FoodGroup.VEGETABLE, None),
    ("h25_03", "Cabbage", FoodGroup.VEGETABLE, None),
    ("h25_04", "Onion", FoodGroup.VEGETABLE, None),
    ("h25_05", "Carrot", FoodGroup.VEGETABLE, None),
    ("h25_06", "Eggplant", FoodGroup.VEGETABLE, None),
    ("h25_07", "Black eggplant", FoodGroup.VEGETABLE, None),
    ("h25_08", "Sweet pepper", FoodGroup.VEGETABLE, None),
    ("h25_09", "Pepper", FoodGroup.VEGETABLE, None),
    ("h25_10", "Cauliflower", FoodGroup.VEGETABLE, None),
    ("h25_11", "French beans", FoodGroup.VEGETABLE, None),
    ("h25_12", "Beetroot", FoodGroup.VEGETABLE, None),
    ("h25_13", "Garlic", FoodGroup.VEGETABLE, None),
    ("h25_14", "Lettuce", FoodGroup.VEGETABLE, None),
    ("h25_15", "Broccoli", FoodGroup.VEGETABLE, None),
    ("h25_16", "Spinach", FoodGroup.VEGETABLE, None),
    ("h25_17", "Celery", FoodGroup.VEGETABLE, None),
    ("h25_18", "Leeks", FoodGroup.VEGETABLE, None),
    ("h25_19", "Pumpkin", FoodGroup.VEGETABLE, None),
    ("h25_20", "Cucumber", FoodGroup.VEGETABLE, None),
    ("h25_21", "Mushroom", FoodGroup.FUNGI, None),
    ("h25_22", "Chayote", FoodGroup.VEGETABLE, None),
    ("h25_23", "Cassava leaves", FoodGroup.VEGETABLE, None),
    ("h25_24", "Other vegetables", FoodGroup.VEGETABLE, None),
    ("h28b1_01", "Avocado", FoodGroup.FRUIT, "h28b2_01"),
    ("h28b1_02", "Orange", FoodGroup.FRUIT, "h28b2_02"),
    ("h28b1_03", "Papaya", FoodGroup.FRUIT, "h28b2_03"),
    ("h28b1_04", "Guava", FoodGroup.FRUIT, "h28b2_04"),
    ("h28b1_05", "Lemon", FoodGroup.FRUIT, "h28b2_05"),
    ("h28b1_06", "Mango", FoodGroup.FRUIT, "h28b2_06"),
    ("h28b1_07", "Mandarin", FoodGroup.FRUIT, "h28b2_07"),
    ("h28b1_08", "Jack fruit", FoodGroup.FRUIT, "h28b2_08"),
    ("h28b1_09", "Beefheart", FoodGroup.FRUIT, "h28b2_09"),
    ("h28b1_10", "Passion fruit", FoodGroup.FRUIT, "h28b2_10"),
    ("h28b1_11", "Pineapple", FoodGroup.FRUIT, "h28b2_11"),
    ("h28b1_12", "Tree tomato", FoodGroup.FRUIT, "h28b2_12"),
    ("h28b1_13", "Watermelon", FoodGroup.FRUIT, "h28b2_13"),
    ("h28b1_14", "Strawberry", FoodGroup.FRUIT, "h28b2_14"),
    ("h28b1_15", "Other fruit", FoodGroup.FRUIT, "h28b2_15"),
)

PROVINCE_ALIASES = {
    "city of kigali": "Kigali City",
    "ville de kigali": "Kigali City",
    "kigali city": "Kigali City",
    "kigali": "Kigali City",
    "umujyi wa kigali": "Kigali City",
    "northern": "Northern",
    "northern province": "Northern",
    "amajyaruguru": "Northern",
    "north": "Northern",
    "southern": "Southern",
    "southern province": "Southern",
    "amajyepfo": "Southern",
    "south": "Southern",
    "eastern": "Eastern",
    "eastern province": "Eastern",
    "iburasirazuba": "Eastern",
    "east": "Eastern",
    "western": "Western",
    "western province": "Western",
    "iburengerazuba": "Western",
    "west": "Western",
}

AREA_ALIASES = {"urban": "Urban", "rural": "Rural", "umugi": "Urban", "icyaro": "Rural"}

SMALL_WORDS = {"of", "and", "the", "de", "la", "wa", "ya", "du"}


def platform_group_for(variable_name: str) -> PlatformGroup:
    name = variable_name.lower()
    if name in EXPLICIT_GROUPS:
        return EXPLICIT_GROUPS[name]
    for pattern, group in _PATTERN_GROUPS:
        if pattern.match(name):
            return group
    return PlatformGroup.UNKNOWN


def section_for(variable_name: str) -> str:
    name = variable_name.lower()
    for pattern, section in SECTION_PREFIXES:
        if pattern.search(name):
            return section
    return "other"


def is_household_variable(variable_name: str) -> bool:
    return section_for(variable_name) in HOUSEHOLD_SECTIONS


def is_person_variable(variable_name: str) -> bool:
    return section_for(variable_name) in PERSON_SECTIONS
