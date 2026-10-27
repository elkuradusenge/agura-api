"""Administrator analysis endpoints.

Food reference items plus the province food summary, mushroom, diversity and
readiness aggregates.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select

from app.core.deps import DbSession, client_context, require_permissions
from app.core.errors import not_found
from app.core.pagination import Page, PageParamsDep, build_page
from app.core.permissions import Permission
from app.models.admin import Admin
from app.models.enums import FoodGroup, GeoLevel
from app.models.household import FoodItem
from app.models.survey import Survey
from app.schemas.analysis import (
    DiversityResponse,
    FoodItemRead,
    FoodItemUpdate,
    FoodSummaryResponse,
    MushroomResponse,
    ReadinessResponse,
)
from app.services import analysis_queries, audit

router = APIRouter(prefix="/admin", tags=["admin.analysis"])

ReadAnalysis = Annotated[Admin, Depends(require_permissions(Permission.ANALYSIS_READ))]
WriteFoodItems = Annotated[Admin, Depends(require_permissions(Permission.FOOD_ITEMS_WRITE))]

FOOD_ITEM_SORTABLE = {
    "standard_name": FoodItem.standard_name,
    "group": FoodItem.group,
    "source_variable": FoodItem.source_variable,
}


def _active_survey(db: DbSession, survey_id: uuid.UUID | None) -> Survey:
    if survey_id is not None:
        survey = db.get(Survey, survey_id)
        if survey is None or survey.deleted_at is not None:
            raise not_found("survey")
        return survey

    survey = db.scalar(
        select(Survey)
        .where(Survey.deleted_at.is_(None), Survey.status.in_(["ready", "completed"]))
        .order_by(Survey.created_at.desc())
        .limit(1)
    )
    if survey is None:
        raise not_found("survey")
    return survey


@router.get("/food-items", response_model=Page[FoodItemRead], summary="List food reference items")
def list_food_items(
    params: PageParamsDep,
    db: DbSession,
    admin: ReadAnalysis,
    q: Annotated[str | None, Query(max_length=120)] = None,
    group: Annotated[FoodGroup | None, Query()] = None,
) -> dict:
    query = select(FoodItem)
    if q:
        pattern = f"%{q.strip().lower()}%"
        query = query.where(
            or_(
                func.lower(FoodItem.standard_name).like(pattern),
                func.lower(FoodItem.local_name).like(pattern),
                func.lower(FoodItem.source_variable).like(pattern),
            )
        )
    if group is not None:
        query = query.where(FoodItem.group == group)

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    column = FOOD_ITEM_SORTABLE.get(params.sort or "standard_name", FoodItem.standard_name)
    ordering = column.asc() if params.order == "asc" else column.desc()
    rows = db.scalars(query.order_by(ordering).offset(params.offset).limit(params.page_size))
    return build_page([FoodItemRead.model_validate(row) for row in rows], total, params)


@router.patch(
    "/food-items/{food_item_id}",
    response_model=FoodItemRead,
    summary="Edit a food reference item",
)
def update_food_item(
    food_item_id: uuid.UUID,
    payload: FoodItemUpdate,
    db: DbSession,
    admin: WriteFoodItems,
    context: Annotated[dict, Depends(client_context)],
) -> FoodItemRead:
    item = db.get(FoodItem, food_item_id)
    if item is None:
        raise not_found("food_item")

    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(item, field, value)
    audit.record(
        db,
        action="food_item.update",
        entity_type="food_item",
        actor_id=admin.id,
        actor_label=admin.email or admin.name,
        entity_id=item.id,
        payload=changes,
        ip_address=context["ip_address"],
        user_agent=context["user_agent"],
    )
    db.commit()
    db.refresh(item)
    return FoodItemRead.model_validate(item)


@router.get(
    "/analysis/province-food-summary",
    response_model=FoodSummaryResponse,
    summary="Food share by province, district and sector",
)
def province_food_summary(
    params: PageParamsDep,
    db: DbSession,
    admin: ReadAnalysis,
    survey_id: Annotated[uuid.UUID | None, Query()] = None,
    level: Annotated[GeoLevel | None, Query()] = None,
    province: Annotated[str | None, Query(max_length=120)] = None,
    district: Annotated[str | None, Query(max_length=120)] = None,
    food_item_id: Annotated[uuid.UUID | None, Query()] = None,
) -> FoodSummaryResponse:
    survey = _active_survey(db, survey_id)
    analysis_queries.require_survey_aggregates(db, survey)
    items, total = analysis_queries.food_summary_items(
        db,
        survey,
        params,
        level=level,
        province=province,
        district=district,
        food_item_id=food_item_id,
    )
    return FoodSummaryResponse(
        meta=analysis_queries.build_meta(db, survey, weighted=True),
        items=items,
        pagination={
            "page": params.page,
            "page_size": params.page_size,
            "total": total,
            "total_pages": (total + params.page_size - 1) // params.page_size,
        },
    )


@router.get(
    "/analysis/mushroom",
    response_model=MushroomResponse,
    summary="Mushroom households by geography with the launch breakdowns",
)
def mushroom_analysis(
    params: PageParamsDep,
    db: DbSession,
    admin: ReadAnalysis,
    survey_id: Annotated[uuid.UUID | None, Query()] = None,
    level: Annotated[GeoLevel | None, Query()] = None,
    province: Annotated[str | None, Query(max_length=120)] = None,
    district: Annotated[str | None, Query(max_length=120)] = None,
) -> MushroomResponse:
    survey = _active_survey(db, survey_id)
    analysis_queries.require_survey_aggregates(db, survey)
    items, total = analysis_queries.mushroom_items(
        db, survey, params, level=level, province=province, district=district
    )
    return MushroomResponse(
        meta=analysis_queries.build_meta(db, survey, weighted=True),
        items=items,
        pagination={
            "page": params.page,
            "page_size": params.page_size,
            "total": total,
            "total_pages": (total + params.page_size - 1) // params.page_size,
        },
    )


@router.get(
    "/analysis/diversity",
    response_model=DiversityResponse,
    summary="Household food diversity by geography",
)
def diversity_analysis(
    params: PageParamsDep,
    db: DbSession,
    admin: ReadAnalysis,
    survey_id: Annotated[uuid.UUID | None, Query()] = None,
    level: Annotated[GeoLevel | None, Query()] = None,
    province: Annotated[str | None, Query(max_length=120)] = None,
    district: Annotated[str | None, Query(max_length=120)] = None,
) -> DiversityResponse:
    survey = _active_survey(db, survey_id)
    analysis_queries.require_survey_aggregates(db, survey)
    items, total = analysis_queries.diversity_items(
        db, survey, params, level=level, province=province, district=district
    )
    return DiversityResponse(
        meta=analysis_queries.build_meta(db, survey, weighted=True),
        items=items,
        pagination={
            "page": params.page,
            "page_size": params.page_size,
            "total": total,
            "total_pages": (total + params.page_size - 1) // params.page_size,
        },
    )


@router.get(
    "/analysis/readiness",
    response_model=ReadinessResponse,
    summary="Readiness index by geography with its components and weights",
)
def readiness_analysis(
    params: PageParamsDep,
    db: DbSession,
    admin: ReadAnalysis,
    survey_id: Annotated[uuid.UUID | None, Query()] = None,
    level: Annotated[GeoLevel | None, Query()] = None,
    province: Annotated[str | None, Query(max_length=120)] = None,
    district: Annotated[str | None, Query(max_length=120)] = None,
) -> ReadinessResponse:
    survey = _active_survey(db, survey_id)
    analysis_queries.require_survey_aggregates(db, survey)
    items, total = analysis_queries.readiness_items(
        db, survey, params, level=level, province=province, district=district
    )
    return ReadinessResponse(
        meta=analysis_queries.build_meta(db, survey, weighted=True),
        items=items,
        pagination={
            "page": params.page,
            "page_size": params.page_size,
            "total": total,
            "total_pages": (total + params.page_size - 1) // params.page_size,
        },
    )
