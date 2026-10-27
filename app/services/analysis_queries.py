"""Building suppressed analysis responses from the stored aggregates."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analysis.suppression import cell, share, weighted_share
from app.core.errors import not_found
from app.core.pagination import PageParams
from app.models.analysis import (
    HouseholdDiversitySummary,
    MushroomSummary,
    ProvinceFoodSummary,
    ReadinessSummary,
)
from app.models.enums import GeoLevel
from app.models.household import FoodItem
from app.models.survey import Survey
from app.schemas.analysis import (
    AnalysisMeta,
    DiversityCell,
    FoodSummaryRow,
    MushroomCell,
    ReadinessCell,
    ReadinessComponent,
    ShareValue,
    SuppressedValue,
)
from app.services import settings_service


def analysis_source(db: Session) -> tuple[str, int]:
    default_name = "Rwanda Population and Housing Census"
    name = settings_service.get_value(db, "analysis.source_name", default_name)
    year = settings_service.get_value(db, "analysis.source_year", 2022)
    return str(name), int(year)


def build_meta(db: Session, survey: Survey, *, weighted: bool) -> AnalysisMeta:
    source_name, source_year = analysis_source(db)
    return AnalysisMeta(
        survey_id=survey.id,
        survey_name=survey.name,
        survey_year=survey.year,
        source_name=source_name,
        source_year=source_year,
        small_cell_threshold=settings_service.small_cell_threshold(db),
        weighted=weighted,
    )


def _suppressed(value: float | int | None, count: int | None, threshold: int) -> dict[str, Any]:
    return cell(value, count, threshold).as_dict()


def _share_value(
    numerator: int,
    denominator: int,
    numerator_weighted: float,
    denominator_weighted: float,
    threshold: int,
    *,
    weighted: bool,
) -> dict[str, Any]:
    """A ratio is only released when its numerator cell is large enough."""
    if numerator < threshold:
        return ShareValue(**cell(None, numerator, threshold).as_dict()).model_dump()
    value = (
        weighted_share(numerator_weighted, denominator_weighted)
        if weighted
        else share(numerator, denominator)
    )
    return ShareValue(**cell(value, numerator, threshold).as_dict()).model_dump()


def food_summary_items(
    db: Session,
    survey: Survey,
    params: PageParams,
    *,
    level: GeoLevel | None,
    province: str | None,
    district: str | None,
    food_item_id: uuid.UUID | None,
) -> tuple[list[FoodSummaryRow], int]:
    threshold = settings_service.small_cell_threshold(db)

    query = (
        select(ProvinceFoodSummary, FoodItem)
        .join(FoodItem, FoodItem.id == ProvinceFoodSummary.food_item_id)
        .where(ProvinceFoodSummary.survey_id == survey.id)
    )
    if level is not None:
        query = query.where(ProvinceFoodSummary.level == level)
    if province:
        query = query.where(ProvinceFoodSummary.province == province)
    if district:
        query = query.where(ProvinceFoodSummary.district == district)
    if food_item_id is not None:
        query = query.where(ProvinceFoodSummary.food_item_id == food_item_id)

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0

    sort_column = {
        "province": ProvinceFoodSummary.province,
        "district": ProvinceFoodSummary.district,
        "sector": ProvinceFoodSummary.sector,
        "food": FoodItem.standard_name,
        "households": ProvinceFoodSummary.households_with_food,
    }.get(params.sort or "food", FoodItem.standard_name)
    ordering = sort_column.asc() if params.order == "asc" else sort_column.desc()
    rows = db.execute(
        query.order_by(ordering, FoodItem.standard_name)
        .offset(params.offset)
        .limit(params.page_size)
    ).all()

    items: list[FoodSummaryRow] = []
    for row, food in rows:
        weighted_numerator = float(row.weighted_households_with_food)
        weighted_total = float(row.weighted_households_total)

        items.append(
            FoodSummaryRow(
                food_item_id=food.id,
                standard_name=food.standard_name,
                group=food.group,
                level=row.level,
                province=row.province,
                district=row.district,
                sector=row.sector,
                households_total=SuppressedValue(
                    **_suppressed(row.households_total, row.households_total, threshold)
                ),
                households_with_food=SuppressedValue(
                    **_suppressed(row.households_with_food, row.households_with_food, threshold)
                ),
                share=ShareValue(
                    **_share_value(
                        row.households_with_food,
                        row.households_total,
                        weighted_numerator,
                        weighted_total,
                        threshold,
                        weighted=True,
                    )
                ),
                unweighted_share=ShareValue(
                    **_share_value(
                        row.households_with_food,
                        row.households_total,
                        weighted_numerator,
                        weighted_total,
                        threshold,
                        weighted=False,
                    )
                ),
                average_count=(
                    SuppressedValue(
                        **_suppressed(row.average_count, row.average_count_basis, threshold)
                    )
                    if row.average_count is not None
                    else None
                ),
                rearing_district_distribution=row.rearing_district_distribution,
            )
        )
    return items, total


def mushroom_items(
    db: Session,
    survey: Survey,
    params: PageParams,
    *,
    level: GeoLevel | None,
    province: str | None,
    district: str | None,
) -> tuple[list[MushroomCell], int]:
    threshold = settings_service.small_cell_threshold(db)

    query = select(MushroomSummary).where(MushroomSummary.survey_id == survey.id)
    if level is not None:
        query = query.where(MushroomSummary.level == level)
    if province:
        query = query.where(MushroomSummary.province == province)
    if district:
        query = query.where(MushroomSummary.district == district)

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    sort_column = {
        "province": MushroomSummary.province,
        "district": MushroomSummary.district,
        "sector": MushroomSummary.sector,
        "households": MushroomSummary.mushroom_households,
    }.get(params.sort or "households", MushroomSummary.mushroom_households)
    ordering = sort_column.asc() if params.order == "asc" else sort_column.desc()
    rows = db.scalars(query.order_by(ordering).offset(params.offset).limit(params.page_size))

    items: list[MushroomCell] = []
    for row in rows:
        mushroom = int(row.mushroom_households)
        weighted_mushroom = float(row.weighted_mushroom_households)
        weighted_total = float(row.weighted_households_total)
        area_denominator = int(row.urban_mushroom_households) + int(row.rural_mushroom_households)
        items.append(
            MushroomCell(
                level=row.level,
                province=row.province,
                district=row.district,
                sector=row.sector,
                households_total=SuppressedValue(
                    **_suppressed(row.households_total, row.households_total, threshold)
                ),
                mushroom_households=SuppressedValue(**_suppressed(mushroom, mushroom, threshold)),
                share=ShareValue(
                    **_share_value(
                        mushroom,
                        int(row.households_total),
                        weighted_mushroom,
                        weighted_total,
                        threshold,
                        weighted=True,
                    )
                ),
                unweighted_share=ShareValue(
                    **_share_value(
                        mushroom,
                        int(row.households_total),
                        weighted_mushroom,
                        weighted_total,
                        threshold,
                        weighted=False,
                    )
                ),
                urban_mushroom_households=SuppressedValue(
                    **_suppressed(
                        row.urban_mushroom_households, row.urban_mushroom_households, threshold
                    )
                ),
                rural_mushroom_households=SuppressedValue(
                    **_suppressed(
                        row.rural_mushroom_households, row.rural_mushroom_households, threshold
                    )
                ),
                urban_share_of_mushroom=ShareValue(
                    **_share_value(
                        int(row.urban_mushroom_households),
                        area_denominator,
                        float(row.weighted_urban_mushroom),
                        float(row.weighted_urban_mushroom) + float(row.weighted_rural_mushroom),
                        threshold,
                        weighted=True,
                    )
                ),
                rural_share_of_mushroom=ShareValue(
                    **_share_value(
                        int(row.rural_mushroom_households),
                        area_denominator,
                        float(row.weighted_rural_mushroom),
                        float(row.weighted_urban_mushroom) + float(row.weighted_rural_mushroom),
                        threshold,
                        weighted=True,
                    )
                ),
                female_headed_mushroom_households=SuppressedValue(
                    **_suppressed(
                        row.female_headed_mushroom_households,
                        row.female_headed_mushroom_households,
                        threshold,
                    )
                ),
                female_headed_share=ShareValue(
                    **_share_value(
                        int(row.female_headed_mushroom_households),
                        mushroom,
                        float(row.weighted_female_headed_mushroom),
                        weighted_mushroom,
                        threshold,
                        weighted=True,
                    )
                ),
                average_household_size=SuppressedValue(
                    **_suppressed(row.average_household_size, int(row.households_total), threshold)
                ),
                mushroom_average_household_size=SuppressedValue(
                    **_suppressed(row.mushroom_average_household_size, mushroom, threshold)
                ),
                existing_activity_households=SuppressedValue(
                    **_suppressed(
                        row.existing_activity_households,
                        row.existing_activity_households,
                        threshold,
                    )
                ),
                existing_activity_share=ShareValue(
                    **_share_value(
                        int(row.existing_activity_households),
                        mushroom,
                        float(row.weighted_existing_activity),
                        weighted_mushroom,
                        threshold,
                        weighted=True,
                    )
                ),
            )
        )
    return items, total


def diversity_items(
    db: Session,
    survey: Survey,
    params: PageParams,
    *,
    level: GeoLevel | None,
    province: str | None,
    district: str | None,
) -> tuple[list[DiversityCell], int]:
    threshold = settings_service.small_cell_threshold(db)

    query = select(HouseholdDiversitySummary).where(
        HouseholdDiversitySummary.survey_id == survey.id
    )
    if level is not None:
        query = query.where(HouseholdDiversitySummary.level == level)
    if province:
        query = query.where(HouseholdDiversitySummary.province == province)
    if district:
        query = query.where(HouseholdDiversitySummary.district == district)

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    sort_column = {
        "province": HouseholdDiversitySummary.province,
        "district": HouseholdDiversitySummary.district,
        "sector": HouseholdDiversitySummary.sector,
        "average": HouseholdDiversitySummary.average_diversity,
    }.get(params.sort or "province", HouseholdDiversitySummary.province)
    ordering = sort_column.asc() if params.order == "asc" else sort_column.desc()
    rows = db.scalars(query.order_by(ordering).offset(params.offset).limit(params.page_size))

    items: list[DiversityCell] = []
    for row in rows:
        households = int(row.households_total)
        items.append(
            DiversityCell(
                level=row.level,
                province=row.province,
                district=row.district,
                sector=row.sector,
                households_total=SuppressedValue(**_suppressed(households, households, threshold)),
                average_diversity=SuppressedValue(
                    **_suppressed(row.average_diversity, households, threshold)
                ),
                weighted_average_diversity=SuppressedValue(
                    **_suppressed(row.weighted_average_diversity, households, threshold)
                ),
                distribution=(row.distribution or {}) if households >= threshold else None,
            )
        )
    return items, total


def readiness_items(
    db: Session,
    survey: Survey,
    params: PageParams,
    *,
    level: GeoLevel | None,
    province: str | None,
    district: str | None,
) -> tuple[list[ReadinessCell], int]:
    threshold = settings_service.small_cell_threshold(db)

    query = select(ReadinessSummary).where(ReadinessSummary.survey_id == survey.id)
    if level is not None:
        query = query.where(ReadinessSummary.level == level)
    if province:
        query = query.where(ReadinessSummary.province == province)
    if district:
        query = query.where(ReadinessSummary.district == district)

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    sort_column = {
        "province": ReadinessSummary.province,
        "district": ReadinessSummary.district,
        "sector": ReadinessSummary.sector,
        "score": ReadinessSummary.score,
    }.get(params.sort or "score", ReadinessSummary.score)
    ordering = sort_column.asc() if params.order == "asc" else sort_column.desc()
    rows = db.scalars(query.order_by(ordering).offset(params.offset).limit(params.page_size))

    items: list[ReadinessCell] = []
    for row in rows:
        households = int(row.households_total)
        components = {
            name: ReadinessComponent(
                households=(
                    None if (value.get("households") or 0) < threshold else value.get("households")
                ),
                weighted_share=(
                    None
                    if (value.get("households") or 0) < threshold
                    else value.get("weighted_share")
                ),
                weight=value.get("weight"),
                definition=value.get("definition"),
                definition_source=value.get("definition_source"),
                suppressed=(value.get("households") or 0) < threshold,
            )
            for name, value in (row.components or {}).items()
        }
        items.append(
            ReadinessCell(
                level=row.level,
                province=row.province,
                district=row.district,
                sector=row.sector,
                households_total=SuppressedValue(**_suppressed(households, households, threshold)),
                score=SuppressedValue(**_suppressed(row.score, households, threshold)),
                components=components,
                weights={key: float(value) for key, value in (row.weights or {}).items()},
            )
        )
    return items, total


def require_survey_aggregates(db: Session, survey: Survey) -> None:
    rows = db.scalar(
        select(func.count())
        .select_from(ProvinceFoodSummary)
        .where(ProvinceFoodSummary.survey_id == survey.id)
    )
    if not rows:
        raise not_found("analysis")
