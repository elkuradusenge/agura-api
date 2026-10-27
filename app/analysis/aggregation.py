"""Aggregation stage.

Everything here is SQL over the loaded households and persons so that a national
file never has to be pulled into memory. Weights are summed rather than averaged,
so weighted estimates and unweighted counts are always available side by side.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.analysis.dictionary import (
    DictionaryEntry,
    codes_for_keywords,
    load_dictionary,
)
from app.models.analysis import (
    HouseholdDiversitySummary,
    MushroomSummary,
    ProvinceFoodSummary,
    ReadinessSummary,
)
from app.models.enums import FoodGroup, GeoLevel
from app.models.household import FoodItem
from app.models.survey import FieldMapping, Survey
from app.services import settings_service

logger = logging.getLogger("agura.analysis.aggregation")

MUSHROOM_VARIABLE = "h25_21"

# One row per geography level: the grouping sets below produce all four in a single scan.
GEO_GROUPING_SETS = (
    "(ml01, ml02, ml03)",
    "(ml01, ml02)",
    "(ml01)",
    "()",
)

GEO_LEVEL_FOR_SETS = (
    GeoLevel.SECTOR,
    GeoLevel.DISTRICT,
    GeoLevel.PROVINCE,
    GeoLevel.NATIONAL,
)

DEFAULT_READINESS_DEFINITIONS: dict[str, dict[str, Any]] = {
    "shelter": {
        "variable": "h06",
        "label_keywords": ["brick", "concrete", "cement", "block", "stone", "burnt"],
        "roof_variable": "h05",
        "roof_label_keywords": ["iron", "tile", "concrete", "sheet", "metal"],
    },
    "water": {
        "variable": "h12",
        "label_keywords": ["piped", "protected", "tap", "public tap", "borehole"],
    },
    "electricity": {
        "variable": "h14",
        "label_keywords": ["yes"],
    },
    "phone": {
        "variable": "p36c",
        "label_keywords": ["smart", "feature"],
    },
    "internet": {
        "variable": "p35",
        "label_keywords": ["yes", "daily", "weekly", "monthly", "occasionally"],
    },
    "education": {
        "variable": "p30a",
        "label_keywords": ["secondary", "university", "higher", "tvet", "vocational", "tertiary"],
    },
}


def readiness_definitions(db: Session) -> dict[str, dict[str, Any]]:
    stored = settings_service.get_value(db, "readiness.definitions")
    if isinstance(stored, dict) and stored:
        return stored
    return DEFAULT_READINESS_DEFINITIONS


def readiness_weights(db: Session) -> dict[str, float]:
    stored = settings_service.get_value(db, "readiness.weights", {}) or {}
    weights = {
        "shelter": 0.2,
        "water": 0.15,
        "electricity": 0.15,
        "phone": 0.1,
        "internet": 0.1,
        "education": 0.2,
        "household_size": 0.1,
    }
    for key, value in stored.items():
        if isinstance(value, int | float):
            weights[key] = float(value)
    return weights


@dataclass
class AggregationResult:
    food_rows: int = 0
    mushroom_rows: int = 0
    diversity_rows: int = 0
    readiness_rows: int = 0
    foods_scored: int = 0
    mushroom_households_national: int | None = None
    mushroom_share_national: float | None = None
    warnings: list[str] = field(default_factory=list)

    def as_summary(self) -> dict[str, Any]:
        return {
            "food_summary_cells": self.food_rows,
            "mushroom_cells": self.mushroom_rows,
            "diversity_cells": self.diversity_rows,
            "readiness_cells": self.readiness_rows,
            "food_items_aggregated": self.foods_scored,
            "mushroom_households_national": self.mushroom_households_national,
            "mushroom_share_national": self.mushroom_share_national,
            "warnings": self.warnings,
        }


def confirmed_yes_codes(db: Session, survey_id: uuid.UUID) -> dict[str, str]:
    rows = db.execute(
        select(FieldMapping.variable_name, FieldMapping.yes_code).where(
            FieldMapping.survey_id == survey_id,
            FieldMapping.confirmed.is_(True),
            FieldMapping.yes_code.is_not(None),
        )
    ).all()
    return {name: str(code) for name, code in rows}


def food_lookup(db: Session, survey_id: uuid.UUID) -> list[dict[str, Any]]:
    """Food items joined to the confirmed yes code for their questionnaire variable."""
    codes = confirmed_yes_codes(db, survey_id)
    items = db.scalars(select(FoodItem).order_by(FoodItem.source_variable)).all()
    lookup: list[dict[str, Any]] = []
    for item in items:
        yes_code = codes.get(item.source_variable.lower())
        if yes_code is None:
            continue
        lookup.append(
            {
                "id": item.id,
                "variable": item.source_variable.lower(),
                "yes_code": yes_code,
                "count_variable": (item.count_variable or "").lower() or None,
                "group": item.group,
                "name": item.standard_name,
            }
        )
    return lookup


def _geo_select() -> str:
    return (
        "ml01 AS province, ml02 AS district, ml03 AS sector, "
        "count(*) AS households_total, "
        "coalesce(sum(hh_weight), 0) AS weighted_households_total"
    )


def _geo_group() -> str:
    return "GROUP BY GROUPING SETS (" + ", ".join(GEO_GROUPING_SETS) + ")"


def _level_for_row(province, district, sector) -> GeoLevel:
    if province is None:
        return GeoLevel.NATIONAL
    if district is None:
        return GeoLevel.PROVINCE
    if sector is None:
        return GeoLevel.DISTRICT
    return GeoLevel.SECTOR


def clear_aggregates(db: Session, survey_id: uuid.UUID) -> None:
    for model in (
        ProvinceFoodSummary,
        MushroomSummary,
        HouseholdDiversitySummary,
        ReadinessSummary,
    ):
        db.execute(delete(model).where(model.survey_id == survey_id))
    db.commit()


def aggregate_food_summaries(db: Session, survey: Survey, lookup: list[dict[str, Any]]) -> int:
    """One pass over households computing every food item at every geography level."""
    if not lookup:
        return 0

    selects = [_geo_select()]
    for index, item in enumerate(lookup):
        variable = item["variable"]
        yes_code = item["yes_code"]
        selects.append(
            f"count(*) FILTER (WHERE attributes ->> '{variable}' = '{yes_code}') "
            f"AS growing_{index}"
        )
        selects.append(
            f"coalesce(sum(hh_weight) FILTER "
            f"(WHERE attributes ->> '{variable}' = '{yes_code}'), 0) "
            f"AS weighted_{index}"
        )
        if item["count_variable"]:
            count_variable = item["count_variable"]
            selects.append(
                f"count(*) FILTER (WHERE attributes ->> '{variable}' = '{yes_code}' "
                f"AND attributes ->> '{count_variable}' ~ '^[0-9]+$') AS countbasis_{index}"
            )
            selects.append(
                f"avg(nullif(attributes ->> '{count_variable}', '')::numeric) FILTER "
                f"(WHERE attributes ->> '{variable}' = '{yes_code}' "
                f"AND attributes ->> '{count_variable}' ~ '^[0-9]+$') AS avgcount_{index}"
            )

    sql = (
        f"SELECT {', '.join(selects)} FROM households WHERE survey_id = :survey_id "
        f"{_geo_group()}"
    )
    rows = db.execute(text(sql), {"survey_id": str(survey.id)}).mappings().all()

    created = 0
    for row in rows:
        level = _level_for_row(row["province"], row["district"], row["sector"])
        households_total = int(row["households_total"] or 0)
        weighted_total = float(row["weighted_households_total"] or 0)
        for index, item in enumerate(lookup):
            db.add(
                ProvinceFoodSummary(
                    survey_id=survey.id,
                    food_item_id=item["id"],
                    level=level,
                    province=row["province"],
                    district=row["district"],
                    sector=row["sector"],
                    households_total=households_total,
                    weighted_households_total=weighted_total,
                    households_with_food=int(row[f"growing_{index}"] or 0),
                    weighted_households_with_food=float(row[f"weighted_{index}"] or 0),
                    average_count=(
                        float(row[f"avgcount_{index}"])
                        if f"avgcount_{index}" in row and row[f"avgcount_{index}"] is not None
                        else None
                    ),
                    average_count_basis=(
                        int(row[f"countbasis_{index}"])
                        if f"countbasis_{index}" in row and row[f"countbasis_{index}"] is not None
                        else None
                    ),
                )
            )
            created += 1
    db.commit()
    return created


def aggregate_livestock_districts(
    db: Session, survey: Survey, lookup: list[dict[str, Any]]
) -> None:
    """District where each animal is reared, stored on the province level row."""
    livestock = [item for item in lookup if item["group"] == FoodGroup.LIVESTOCK]
    if not livestock:
        return

    districts_by_item = _livestock_district_variables(db, survey.id)
    for item in livestock:
        district_variable = districts_by_item.get(item["variable"])
        if district_variable is None:
            continue
        rows = db.execute(
            text(
                f"""
                SELECT ml01 AS province,
                       attributes ->> '{district_variable}' AS rearing_district,
                       count(*) AS households
                FROM households
                WHERE survey_id = :survey_id
                  AND attributes ->> '{item["variable"]}' = :yes_code
                  AND attributes ->> '{district_variable}' IS NOT NULL
                GROUP BY ml01, 2
                """
            ),
            {"survey_id": str(survey.id), "yes_code": item["yes_code"]},
        ).all()
        by_province: dict[str | None, dict[str, int]] = {}
        for province, rearing_district, households in rows:
            by_province.setdefault(province, {})[str(rearing_district)] = int(households)
        for province, distribution in by_province.items():
            db.execute(
                text(
                    """
                    UPDATE province_food_summary
                    SET rearing_district_distribution = CAST(:distribution AS jsonb)
                    WHERE survey_id = :survey_id AND food_item_id = :food_item_id
                      AND level = 'province' AND province IS NOT DISTINCT FROM :province
                    """
                ),
                {
                    "distribution": _json(distribution),
                    "survey_id": str(survey.id),
                    "food_item_id": str(item["id"]),
                    "province": province,
                },
            )
    db.commit()


def _livestock_district_variables(db: Session, survey_id: uuid.UUID) -> dict[str, str]:
    """h22a_01 pairs with h22b2_01, and so on."""
    rows = db.execute(
        select(FieldMapping.variable_name).where(FieldMapping.survey_id == survey_id)
    ).scalars()
    names = {name.lower() for name in rows}
    pairs: dict[str, str] = {}
    for name in names:
        if name.startswith("h22a_"):
            candidate = "h22b2_" + name[len("h22a_") :]
            if candidate in names:
                pairs[name] = candidate
    return pairs


def _json(payload: Any) -> str:
    import json

    return json.dumps(payload, default=str)


def aggregate_mushroom(db: Session, survey: Survey) -> int:
    """Mushroom households by geography, with the extra breakdowns the product needs."""
    codes = confirmed_yes_codes(db, survey.id)
    yes_code = codes.get(MUSHROOM_VARIABLE)
    if yes_code is None:
        logger.info("Mushroom variable is not mapped for survey %s", survey.id)
        return 0

    existing_activity = _existing_activity_expression(db, survey.id, codes)
    sql = f"""
        SELECT
            {_geo_select()},
            count(*) FILTER (WHERE attributes ->> '{MUSHROOM_VARIABLE}' = :yes_code)
                AS mushroom_households,
            coalesce(sum(hh_weight) FILTER (
                WHERE attributes ->> '{MUSHROOM_VARIABLE}' = :yes_code), 0)
                AS weighted_mushroom_households,
            count(*) FILTER (WHERE attributes ->> '{MUSHROOM_VARIABLE}' = :yes_code
                AND ml07 = 'Urban') AS urban_mushroom,
            count(*) FILTER (WHERE attributes ->> '{MUSHROOM_VARIABLE}' = :yes_code
                AND ml07 = 'Rural') AS rural_mushroom,
            coalesce(sum(hh_weight) FILTER (WHERE attributes ->> '{MUSHROOM_VARIABLE}' = :yes_code
                AND ml07 = 'Urban'), 0) AS weighted_urban_mushroom,
            coalesce(sum(hh_weight) FILTER (WHERE attributes ->> '{MUSHROOM_VARIABLE}' = :yes_code
                AND ml07 = 'Rural'), 0) AS weighted_rural_mushroom,
            count(*) FILTER (WHERE female_headed IS NOT NULL) AS heads_known,
            count(*) FILTER (WHERE attributes ->> '{MUSHROOM_VARIABLE}' = :yes_code
                AND female_headed) AS female_headed_mushroom,
            coalesce(sum(hh_weight) FILTER (WHERE attributes ->> '{MUSHROOM_VARIABLE}' = :yes_code
                AND female_headed), 0) AS weighted_female_headed_mushroom,
            avg(household_size) FILTER (WHERE household_size IS NOT NULL)
                AS average_household_size,
            avg(household_size) FILTER (WHERE household_size IS NOT NULL
                AND attributes ->> '{MUSHROOM_VARIABLE}' = :yes_code)
                AS mushroom_average_household_size,
            count(*) FILTER (WHERE attributes ->> '{MUSHROOM_VARIABLE}' = :yes_code
                AND {existing_activity}) AS existing_activity_households,
            coalesce(sum(hh_weight) FILTER (WHERE attributes ->> '{MUSHROOM_VARIABLE}' = :yes_code
                AND {existing_activity}), 0) AS weighted_existing_activity
        FROM households
        WHERE survey_id = :survey_id
        {_geo_group()}
    """
    rows = db.execute(text(sql), {"survey_id": str(survey.id), "yes_code": yes_code}).mappings()

    created = 0
    for row in rows:
        db.add(
            MushroomSummary(
                survey_id=survey.id,
                level=_level_for_row(row["province"], row["district"], row["sector"]),
                province=row["province"],
                district=row["district"],
                sector=row["sector"],
                households_total=int(row["households_total"] or 0),
                weighted_households_total=float(row["weighted_households_total"] or 0),
                mushroom_households=int(row["mushroom_households"] or 0),
                weighted_mushroom_households=float(row["weighted_mushroom_households"] or 0),
                urban_mushroom_households=int(row["urban_mushroom"] or 0),
                rural_mushroom_households=int(row["rural_mushroom"] or 0),
                weighted_urban_mushroom=float(row["weighted_urban_mushroom"] or 0),
                weighted_rural_mushroom=float(row["weighted_rural_mushroom"] or 0),
                female_headed_mushroom_households=int(row["female_headed_mushroom"] or 0),
                weighted_female_headed_mushroom=float(row["weighted_female_headed_mushroom"] or 0),
                households_with_heads_known=int(row["heads_known"] or 0),
                average_household_size=(
                    float(row["average_household_size"])
                    if row["average_household_size"] is not None
                    else None
                ),
                mushroom_average_household_size=(
                    float(row["mushroom_average_household_size"])
                    if row["mushroom_average_household_size"] is not None
                    else None
                ),
                existing_activity_households=int(row["existing_activity_households"] or 0),
                weighted_existing_activity=float(row["weighted_existing_activity"] or 0),
            )
        )
        created += 1
    db.commit()
    return created


def _existing_activity_expression(db: Session, survey_id: uuid.UUID, codes: dict[str, str]) -> str:
    """Grain, fruit or livestock activity: any mapped crop, fruit or livestock flag."""
    items = db.scalars(select(FoodItem)).all()
    clauses: list[str] = []
    for item in items:
        variable = item.source_variable.lower()
        yes_code = codes.get(variable)
        if yes_code is None or item.group not in {
            FoodGroup.FIELD_CROP,
            FoodGroup.FRUIT,
            FoodGroup.LIVESTOCK,
        }:
            continue
        clauses.append(f"attributes ->> '{variable}' = '{yes_code}'")
    if not clauses:
        return "false"
    return "(" + " OR ".join(clauses) + ")"


def aggregate_diversity(db: Session, survey: Survey) -> int:
    """Count the distinct food items each household grows, then summarise by geography."""
    codes = confirmed_yes_codes(db, survey.id)
    items = db.scalars(
        select(FoodItem).where(
            FoodItem.group.in_([FoodGroup.FIELD_CROP, FoodGroup.VEGETABLE, FoodGroup.FRUIT])
        )
    ).all()
    variables = [
        item.source_variable.lower() for item in items if item.source_variable.lower() in codes
    ]
    if not variables:
        return 0

    flags = " + ".join(
        f"(CASE WHEN attributes ->> '{variable}' = '{codes[variable]}' THEN 1 ELSE 0 END)"
        for variable in variables
    )
    db.execute(
        text(
            f"""
            UPDATE households
            SET food_diversity_count = ({flags})
            WHERE survey_id = :survey_id
            """
        ),
        {"survey_id": str(survey.id)},
    )
    db.commit()

    rows = db.execute(
        text(
            f"""
            WITH counted AS (
                SELECT ml01, ml02, ml03,
                       count(*) AS households,
                       coalesce(sum(hh_weight), 0) AS weighted,
                       sum(food_diversity_count) AS diversity_sum,
                       sum(food_diversity_count * coalesce(hh_weight, 0)) AS weighted_sum,
                       jsonb_object_agg(food_diversity_count, value_households) AS distribution
                FROM (
                    SELECT ml01, ml02, ml03, hh_weight, food_diversity_count,
                           count(*) OVER (
                               PARTITION BY ml01, ml02, ml03, food_diversity_count
                           ) AS value_households
                    FROM households
                    WHERE survey_id = :survey_id AND food_diversity_count IS NOT NULL
                ) per_household
                {_geo_group_with_window()}
            )
            SELECT ml01 AS province, ml02 AS district, ml03 AS sector,
                   households AS households_total,
                   weighted AS weighted_households_total,
                   diversity_sum::numeric / nullif(households, 0) AS average_diversity,
                   weighted_sum / nullif(weighted, 0) AS weighted_average_diversity,
                   distribution
            FROM counted
            """
        ),
        {"survey_id": str(survey.id)},
    ).mappings()

    created = 0
    for row in rows:
        db.add(
            HouseholdDiversitySummary(
                survey_id=survey.id,
                level=_level_for_row(row["province"], row["district"], row["sector"]),
                province=row["province"],
                district=row["district"],
                sector=row["sector"],
                households_total=int(row["households_total"] or 0),
                weighted_households_total=float(row["weighted_households_total"] or 0),
                average_diversity=(
                    float(row["average_diversity"])
                    if row["average_diversity"] is not None
                    else None
                ),
                weighted_average_diversity=(
                    float(row["weighted_average_diversity"])
                    if row["weighted_average_diversity"] is not None
                    else None
                ),
                distribution=row["distribution"] or {},
            )
        )
        created += 1
    db.commit()
    return created


def _geo_group_with_window() -> str:
    return (
        "GROUP BY GROUPING SETS ("
        + ", ".join(f"({sets})" for sets in ("ml01, ml02, ml03", "ml01, ml02", "ml01", ""))
        + ")"
    )


def aggregate_readiness(db: Session, survey: Survey) -> tuple[int, list[str]]:
    """Transparent 0 to 100 readiness score per geography unit.

    Returns the number of geography rows written and the component names that could
    not be resolved, so the job summary can tell an administrator what to configure.
    """
    dictionary = load_dictionary(db, survey.id)
    definitions = readiness_definitions(db)
    weights = readiness_weights(db)

    components: dict[str, str] = {}
    component_definitions: dict[str, dict[str, Any]] = {}
    unresolved: list[str] = []
    for name, definition in definitions.items():
        usable = _codes_resolved_from_labels(definition, dictionary)
        expression = _component_expression(definition, dictionary)
        if expression is not None:
            components[name] = expression
            other = dict(definition)
            other["definition_has_labels"] = usable
            component_definitions[name] = other
        else:
            unresolved.append(name)

    if not components:
        logger.info("No readiness components could be resolved for survey %s", survey.id)
        return 0, unresolved

    selects = [_geo_select()]
    for name, expression in components.items():
        selects.append(f"count(*) FILTER (WHERE {expression}) AS count_{name}")
        selects.append(
            f"coalesce(sum(hh_weight) FILTER (WHERE {expression}), 0) AS weighted_{name}"
        )
    selects.append("avg(household_size) FILTER (WHERE household_size IS NOT NULL) AS avg_size")

    sql = (
        f"SELECT {', '.join(selects)} FROM households WHERE survey_id = :survey_id "
        f"{_geo_group()}"
    )
    rows = db.execute(text(sql), {"survey_id": str(survey.id)}).mappings().all()

    created = 0
    for row in rows:
        weighted_total = float(row["weighted_households_total"] or 0)
        households_total = int(row["households_total"] or 0)
        payload: dict[str, Any] = {}
        score = 0.0
        total_weight = 0.0
        for name in components:
            definition = component_definitions.get(name, {})
            definition_has_labels = bool(definition.get("definition_has_labels"))
            count = int(row[f"count_{name}"] or 0)
            weighted = float(row[f"weighted_{name}"] or 0)
            share = weighted / weighted_total if weighted_total else None
            source = "value_labels" if definition_has_labels else "configured_codes"
            payload[name] = {
                "households": count,
                "weighted_households": round(weighted, 4),
                "weighted_share": round(share, 4) if share is not None else None,
                "weight": weights.get(name),
                "definition": definition.get("label_keywords"),
                "definition_source": source,
            }
            if share is not None and weights.get(name):
                score += share * weights[name]
                total_weight += weights[name]

        # Household size is scored against a documented target rather than a share.
        size_weight = weights.get("household_size")
        average_size = row["avg_size"]
        if size_weight and average_size is not None:
            size_share = min(float(average_size) / 5.0, 1.0)
            payload["household_size"] = {
                "households": households_total,
                "average": round(float(average_size), 3),
                "weighted_share": round(size_share, 4),
                "weight": size_weight,
                "definition": "average household size divided by 5, capped at 1",
            }
            score += size_share * size_weight
            total_weight += size_weight

        final_score = round(100.0 * score / total_weight, 2) if total_weight else None
        db.add(
            ReadinessSummary(
                survey_id=survey.id,
                level=_level_for_row(row["province"], row["district"], row["sector"]),
                province=row["province"],
                district=row["district"],
                sector=row["sector"],
                households_total=households_total,
                weighted_households_total=weighted_total,
                components=payload,
                score=final_score,
                weights=weights,
            )
        )
        created += 1
    db.commit()
    return created, unresolved


HEAD_COLUMN_BY_VARIABLE = {
    "p30a": "head_education_code",
    "p35": "head_internet_code",
    "p36c": "head_phone_type_code",
}


def _codes_resolved_from_labels(
    definition: dict[str, Any], dictionary: dict[str, DictionaryEntry]
) -> bool:
    """True when the file value labels supplied the codes, false when settings did."""
    variable = str(definition.get("variable", "")).lower()
    entry = dictionary.get(variable)
    if entry is None:
        return False
    keywords = {str(word).lower() for word in definition.get("label_keywords", [])}
    return bool(codes_for_keywords(entry, keywords))


def _component_expression(
    definition: dict[str, Any], dictionary: dict[str, DictionaryEntry]
) -> str | None:
    """Turn one readiness definition into a SQL boolean expression.

    Person level components read the head columns copied onto the household row, the
    rest read the household attributes directly.
    """
    variable = str(definition.get("variable", "")).lower()
    entry = dictionary.get(variable)
    if entry is None:
        return None

    keywords = {str(word).lower() for word in definition.get("label_keywords", [])}
    codes = codes_for_keywords(entry, keywords)
    if not codes:
        # Some files ship person variables without value labels. In that case only a
        # code list an administrator declared in settings is used, never a guess.
        configured = [str(code) for code in definition.get("codes", [])]
        codes = configured
    if not codes:
        return None

    quoted = ", ".join(f"'{code}'" for code in codes)
    column = HEAD_COLUMN_BY_VARIABLE.get(variable)
    if column is not None:
        expression = f"{column} IN ({quoted})"
    elif variable.startswith("h"):
        expression = f"attributes ->> '{variable}' IN ({quoted})"
    else:
        return None

    # Shelter needs an improved wall and an improved roof, so the second half is anded in.
    roof_variable = str(definition.get("roof_variable", "")).lower()
    roof_entry = dictionary.get(roof_variable) if roof_variable else None
    if roof_entry is not None:
        roof_keywords = {str(word).lower() for word in definition.get("roof_label_keywords", [])}
        roof_codes = codes_for_keywords(roof_entry, roof_keywords)
        if roof_codes:
            roof_quoted = ", ".join(f"'{code}'" for code in roof_codes)
            expression = f"({expression} AND attributes ->> '{roof_variable}' IN ({roof_quoted}))"
    return expression


def run_aggregation(db: Session, survey: Survey) -> AggregationResult:
    """Compute and store every aggregate table for one survey."""
    result = AggregationResult()
    clear_aggregates(db, survey.id)

    lookup = food_lookup(db, survey.id)
    result.foods_scored = len(lookup)
    if not lookup:
        result.warnings.append(
            "No food item has a confirmed yes code, confirm the mapping before aggregating"
        )

    result.food_rows = aggregate_food_summaries(db, survey, lookup)
    aggregate_livestock_districts(db, survey, lookup)
    result.mushroom_rows = aggregate_mushroom(db, survey)
    result.diversity_rows = aggregate_diversity(db, survey)
    result.readiness_rows, unresolved = aggregate_readiness(db, survey)

    national = db.execute(
        text(
            """
            SELECT mushroom_households, weighted_mushroom_households,
                   weighted_households_total
            FROM mushroom_summary
            WHERE survey_id = :survey_id AND level = 'national'
            """
        ),
        {"survey_id": str(survey.id)},
    ).one_or_none()
    if national is not None:
        result.mushroom_households_national = int(national.mushroom_households)
        total = float(national.weighted_households_total or 0)
        if total:
            result.mushroom_share_national = round(
                float(national.weighted_mushroom_households or 0) / total, 4
            )

    if result.mushroom_rows == 0:
        result.warnings.append("Mushroom is not mapped for this survey")
    if unresolved:
        result.warnings.append(
            "Readiness components without usable codes: "
            + ", ".join(sorted(unresolved))
            + ". Set codes in the readiness definitions setting if the file has no value labels."
        )
    return result


__all__ = [
    "AggregationResult",
    "DEFAULT_READINESS_DEFINITIONS",
    "MUSHROOM_VARIABLE",
    "aggregate_diversity",
    "aggregate_food_summaries",
    "aggregate_mushroom",
    "aggregate_readiness",
    "clear_aggregates",
    "food_lookup",
    "readiness_definitions",
    "readiness_weights",
    "run_aggregation",
]
