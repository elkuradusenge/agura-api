"""Administrator AI suggestion and nutrition reference endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from sqlalchemy import func, select

from app.analysis import nutrition as nutrition_module
from app.analysis import ranking as ranking_engine
from app.core.deps import DbSession, client_context, require_permissions
from app.core.errors import not_found, unprocessable
from app.core.pagination import PageParamsDep
from app.core.permissions import Permission
from app.models.admin import Admin
from app.models.enums import FeasibilityLevel, GeoLevel
from app.models.household import FoodItem
from app.models.ranking import AiRankingItem, AiRankingRun, NutritionReference, ProvinceCluster
from app.models.survey import Survey
from app.schemas.ranking import (
    ClusterRead,
    FoodItemFeasibility,
    NutritionImportResult,
    NutritionRow,
    NutritionUpsert,
    RankingItemRead,
    RankingRequest,
    RankingRunDetail,
)
from app.services import analysis_queries, audit

router = APIRouter(prefix="/admin", tags=["admin.ai"])

ReadAnalysis = Annotated[Admin, Depends(require_permissions(Permission.ANALYSIS_READ))]
RunRanking = Annotated[Admin, Depends(require_permissions(Permission.AI_RUN))]
WriteNutrition = Annotated[Admin, Depends(require_permissions(Permission.NUTRITION_WRITE))]
WriteFoodItems = Annotated[Admin, Depends(require_permissions(Permission.FOOD_ITEMS_WRITE))]


@router.post(
    "/ai/rankings",
    response_model=RankingRunDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Run the under-farmed food ranking",
)
def create_ranking(
    payload: RankingRequest,
    db: DbSession,
    admin: RunRanking,
    context: Annotated[dict, Depends(client_context)],
) -> RankingRunDetail:
    survey = _survey(db, payload.survey_id)

    weights = payload.weights.model_dump(exclude_none=True) if payload.weights else None
    try:
        result = ranking_engine.run_ranking(
            db,
            survey,
            weights=weights,
            nutrition_needs=payload.nutrition_needs,
            created_by_id=admin.id,
            provinces=payload.provinces,
        )
    except ValueError as exc:
        raise unprocessable(
            "ai.ranking_failed",
            "errors.ai.ranking_failed",
            f"The ranking could not be produced: {exc}",
        ) from exc

    audit.record(
        db,
        action="ai.ranking_run",
        entity_type="ai_ranking_run",
        actor_id=admin.id,
        actor_label=admin.email or admin.name,
        entity_id=result.run_id,
        payload={
            "survey_id": str(survey.id),
            "score_basis": result.score_basis,
            "items": result.items_written,
        },
        ip_address=context["ip_address"],
        user_agent=context["user_agent"],
    )
    db.commit()
    return _run_detail(db, result.run_id)


@router.get(
    "/ai/rankings/latest",
    response_model=RankingRunDetail,
    summary="Read the most recent ranking",
)
def latest_ranking(
    db: DbSession,
    admin: ReadAnalysis,
    survey_id: Annotated[uuid.UUID | None, Query()] = None,
    province: Annotated[str | None, Query(max_length=120)] = None,
) -> RankingRunDetail:
    run = ranking_engine.latest_run(db, survey_id)
    if run is None:
        raise not_found("ranking")
    return _run_detail(db, run.id, province=province)


@router.get("/ai/rankings/{run_id}", response_model=RankingRunDetail, summary="Read a ranking run")
def get_ranking(
    run_id: uuid.UUID,
    db: DbSession,
    admin: ReadAnalysis,
    province: Annotated[str | None, Query(max_length=120)] = None,
) -> RankingRunDetail:
    return _run_detail(db, run_id, province=province)


@router.post(
    "/nutrition-reference/import",
    response_model=NutritionImportResult,
    summary="Import nutrition values from a CSV",
)
def import_nutrition(
    db: DbSession,
    admin: WriteNutrition,
    context: Annotated[dict, Depends(client_context)],
    file: Annotated[
        UploadFile, File(description="CSV with food_variable, nutrient, amount_per_100g")
    ],
) -> NutritionImportResult:
    content = file.file.read()
    if not content:
        raise unprocessable(
            "nutrition.empty_file",
            "errors.nutrition.empty_file",
            "The uploaded CSV is empty",
        )
    result = nutrition_module.import_nutrition_csv(db, content)
    audit.record(
        db,
        action="nutrition.import",
        entity_type="nutrition_reference",
        actor_id=admin.id,
        actor_label=admin.email or admin.name,
        payload={"imported": result.imported, "skipped": result.skipped},
        ip_address=context["ip_address"],
        user_agent=context["user_agent"],
    )
    db.commit()
    return NutritionImportResult(**result.as_dict())


@router.get("/nutrition-reference", summary="List nutrition reference rows")
def list_nutrition(
    db: DbSession,
    admin: ReadAnalysis,
    params: PageParamsDep,
    nutrient: Annotated[str | None, Query(max_length=60)] = None,
) -> dict:
    query = select(NutritionReference, FoodItem.standard_name).join(
        FoodItem, FoodItem.id == NutritionReference.food_item_id
    )
    if nutrient:
        query = query.where(NutritionReference.nutrient == nutrient.lower())

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.execute(
        query.order_by(FoodItem.standard_name, NutritionReference.nutrient)
        .offset(params.offset)
        .limit(params.page_size)
    ).all()

    items = []
    for record, standard_name in rows:
        payload = NutritionRow.model_validate(record)
        payload.standard_name = standard_name
        items.append(payload)
    return {
        "items": items,
        "pagination": {
            "page": params.page,
            "page_size": params.page_size,
            "total": total,
            "total_pages": (total + params.page_size - 1) // params.page_size,
        },
    }


@router.put(
    "/nutrition-reference",
    response_model=NutritionRow,
    summary="Create or replace one nutrient value",
)
def upsert_nutrition(
    payload: NutritionUpsert,
    db: DbSession,
    admin: WriteNutrition,
    context: Annotated[dict, Depends(client_context)],
) -> NutritionRow:
    food = db.get(FoodItem, payload.food_item_id)
    if food is None:
        raise not_found("food_item")

    nutrient = nutrition_module.normalise_nutrient(payload.nutrient)
    record = db.scalar(
        select(NutritionReference).where(
            NutritionReference.food_item_id == food.id,
            NutritionReference.nutrient == nutrient,
        )
    )
    if record is None:
        record = NutritionReference(food_item_id=food.id, nutrient=nutrient)
        db.add(record)

    record.amount_per_100g = payload.amount_per_100g
    record.unit = payload.unit
    record.source = payload.source
    record.reviewer_name = payload.reviewer_name
    record.review_date = payload.review_date

    audit.record(
        db,
        action="nutrition.upsert",
        entity_type="nutrition_reference",
        actor_id=admin.id,
        actor_label=admin.email or admin.name,
        entity_id=f"{food.id}:{nutrient}",
        payload={"nutrient": nutrient, "amount": payload.amount_per_100g},
        ip_address=context["ip_address"],
        user_agent=context["user_agent"],
    )
    db.commit()
    db.refresh(record)
    result = NutritionRow.model_validate(record)
    result.standard_name = food.standard_name
    return result


@router.delete(
    "/nutrition-reference/{food_item_id}",
    summary="Remove every nutrient value for one food",
)
def delete_nutrition(
    food_item_id: uuid.UUID,
    db: DbSession,
    admin: WriteNutrition,
    context: Annotated[dict, Depends(client_context)],
) -> dict:
    food = db.get(FoodItem, food_item_id)
    if food is None:
        raise not_found("food_item")
    removed = nutrition_module.clear_nutrition(db, food_item_id)
    audit.record(
        db,
        action="nutrition.delete",
        entity_type="nutrition_reference",
        actor_id=admin.id,
        actor_label=admin.email or admin.name,
        entity_id=food.id,
        payload={"removed": removed},
        ip_address=context["ip_address"],
        user_agent=context["user_agent"],
    )
    db.commit()
    return {"removed": removed}


@router.patch(
    "/food-items/{food_item_id}/feasibility",
    summary="Set the feasibility fields used by the ranking",
)
def update_feasibility(
    food_item_id: uuid.UUID,
    payload: FoodItemFeasibility,
    db: DbSession,
    admin: WriteFoodItems,
    context: Annotated[dict, Depends(client_context)],
) -> dict:
    food = db.get(FoodItem, food_item_id)
    if food is None:
        raise not_found("food_item")

    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        if field in {"budget_fit", "land_need", "season_fit"} and value is None:
            setattr(food, field, FeasibilityLevel.UNKNOWN)
        else:
            setattr(food, field, value)

    audit.record(
        db,
        action="food_item.feasibility",
        entity_type="food_item",
        actor_id=admin.id,
        actor_label=admin.email or admin.name,
        entity_id=food.id,
        payload={key: str(value) for key, value in changes.items()},
        ip_address=context["ip_address"],
        user_agent=context["user_agent"],
    )
    db.commit()
    return {
        "id": str(food.id),
        "budget_fit": food.budget_fit.value,
        "land_need": food.land_need.value,
        "season_fit": food.season_fit.value,
        "market_note": food.market_note,
    }


def _survey(db, survey_id: uuid.UUID | None) -> Survey:
    if survey_id is not None:
        survey = db.get(Survey, survey_id)
        if survey is None or survey.deleted_at is not None:
            raise not_found("survey")
        analysis_queries.require_survey_aggregates(db, survey)
        return survey

    survey = db.scalar(
        select(Survey)
        .where(Survey.deleted_at.is_(None), Survey.status.in_(["ready", "completed"]))
        .order_by(Survey.created_at.desc())
        .limit(1)
    )
    if survey is None:
        raise not_found("survey")
    analysis_queries.require_survey_aggregates(db, survey)
    return survey


def _run_detail(
    db, run_id: uuid.UUID, *, province: str | None = None, limit: int = 50
) -> RankingRunDetail:
    run = db.get(AiRankingRun, run_id)
    if run is None:
        raise not_found("ranking")

    query = (
        select(AiRankingItem, FoodItem.standard_name)
        .join(FoodItem, FoodItem.id == AiRankingItem.food_item_id)
        .where(AiRankingItem.run_id == run.id)
    )
    query = query.where(
        AiRankingItem.province.is_(None) if province is None else AiRankingItem.province == province
    )
    rows = db.execute(query.order_by(AiRankingItem.rank).limit(limit)).all()

    items = []
    for item, standard_name in rows:
        payload = RankingItemRead.model_validate(item)
        payload.standard_name = standard_name
        items.append(payload)

    clusters = db.scalars(
        select(ProvinceCluster)
        .where(ProvinceCluster.run_id == run.id, ProvinceCluster.level == GeoLevel.PROVINCE.value)
        .order_by(ProvinceCluster.cluster_label, ProvinceCluster.unit)
    ).all()

    detail = RankingRunDetail.model_validate(run)
    detail.items = items
    detail.clusters = [ClusterRead.model_validate(cluster) for cluster in clusters]
    return detail
