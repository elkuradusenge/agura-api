"""Under-farmed food suggestions.

Three steps, all reproducible from stored inputs:

1. gap score: 1 minus the weighted share of households growing a food, scaled against
   the median share of the foods in the same group, so a rarely grown vegetable is
   compared with vegetables rather than with staples.
2. clustering: provinces and districts are grouped by their food share vectors with
   KMeans, and k is chosen by silhouette score between 3 and 6.
3. ranking: a weighted blend of nutrition value, nutrition need, feasibility and gap.

The engine only suggests. An administrator decides what becomes a product, and no
health claim is ever produced here.
"""

from __future__ import annotations

import json
import logging
import uuid
import warnings
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np
from sklearn.cluster import KMeans
from sklearn.exceptions import ConvergenceWarning
from sklearn.metrics import silhouette_score
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.analysis import nutrition as nutrition_module
from app.models.enums import FeasibilityLevel, FoodGroup, GeoLevel, RankingStatus
from app.models.household import FoodItem
from app.models.ranking import AiRankingItem, AiRankingRun, ProvinceCluster
from app.models.survey import Survey
from app.services import settings_service

logger = logging.getLogger("agura.analysis.ranking")

MUSHROOM_VARIABLE = "h25_21"
MUSHROOM_PIN_REASON = "Selected by the team for the first release"
MIN_CLUSTERS = 3
MAX_CLUSTERS = 6
DEFAULT_WEIGHTS = {
    "nutrition_value": 0.3,
    "nutrition_need": 0.3,
    "feasibility": 0.2,
    "gap": 0.2,
}
FEASIBILITY_SCORE = {
    FeasibilityLevel.HIGH: 1.0,
    FeasibilityLevel.MEDIUM: 0.6,
    FeasibilityLevel.LOW: 0.3,
    FeasibilityLevel.UNKNOWN: 0.5,
}


@dataclass
class RankingResult:
    run_id: uuid.UUID
    score_basis: str
    cluster_count: int | None
    silhouette: float | None
    items_written: int
    warnings: list[str] = field(default_factory=list)
    top_under_farmed: list[dict[str, Any]] = field(default_factory=list)


def resolve_weights(db: Session, requested: dict[str, float] | None) -> dict[str, float]:
    stored = settings_service.get_value(db, "ai.ranking_weights", {}) or {}
    weights = dict(DEFAULT_WEIGHTS)
    for key, value in stored.items():
        if key in weights and isinstance(value, int | float):
            weights[key] = float(value)
    if requested:
        for key, value in requested.items():
            if key in weights and value is not None:
                weights[key] = max(0.0, float(value))
    total = sum(weights.values())
    if total <= 0:
        weights = dict(DEFAULT_WEIGHTS)
        total = sum(weights.values())
    return {key: round(value / total, 4) for key, value in weights.items()}


def resolve_nutrition_needs(db: Session, requested: dict[str, float] | None) -> dict[str, float]:
    stored = settings_service.get_value(db, "analysis.nutrition_need_weights", {}) or {}
    needs = {key: float(value) for key, value in stored.items() if isinstance(value, int | float)}
    if not needs:
        needs = {"iron": 1.0, "zinc": 1.0, "vitamin_a": 1.0, "protein": 1.0}
    if requested:
        for key, value in requested.items():
            if value is not None:
                needs[key] = max(0.0, float(value))
    return needs


def _share_map(
    db: Session, survey: Survey, level: GeoLevel
) -> dict[str | None, dict[uuid.UUID, float]]:
    """Weighted share per geography unit and food item."""
    rows = db.execute(
        text(
            """
            SELECT province, district, food_item_id,
                   weighted_households_with_food, weighted_households_total
            FROM province_food_summary
            WHERE survey_id = :survey_id AND level = :level
            """
        ),
        {"survey_id": str(survey.id), "level": level.value},
    ).all()

    result: dict[str | None, dict[uuid.UUID, float]] = defaultdict(dict)
    for province, district, food_id, weighted_food, weighted_total in rows:
        unit = province if level is GeoLevel.PROVINCE else district
        total = float(weighted_total or 0)
        result[unit][food_id] = float(weighted_food or 0) / total if total else 0.0
    return result


def gap_scores(
    shares: dict[uuid.UUID, float], groups: dict[uuid.UUID, FoodGroup]
) -> dict[uuid.UUID, float]:
    """1 minus share, scaled against the median share of the same food group.

    A food nobody grows scores 1.0. A food that is already the most common in its group
    scores near 0. Foods in groups where everything is common therefore do not dominate.
    """
    medians: dict[FoodGroup, float] = {}
    grouped: dict[FoodGroup, list[float]] = defaultdict(list)
    for food_id, share_value in shares.items():
        grouped[groups.get(food_id, FoodGroup.FIELD_CROP)].append(share_value)
    for group, values in grouped.items():
        medians[group] = float(np.median(values)) if values else 0.0

    scores: dict[uuid.UUID, float] = {}
    for food_id, share_value in shares.items():
        group = groups.get(food_id, FoodGroup.FIELD_CROP)
        median = medians.get(group, 0.0)
        if median <= 0:
            scores[food_id] = 1.0 - min(share_value, 1.0)
            continue
        relative = min(share_value / median, 2.0)
        scores[food_id] = max(0.0, min(1.0, 1.0 - relative * 0.5))
    return scores


@dataclass
class ClusteringOutcome:
    labels: dict[str, str]
    cluster_count: int
    silhouette: float | None
    summaries: dict[str, dict[str, Any]]


def cluster_units(
    matrix: dict[str, dict[uuid.UUID, float]],
    names: dict[uuid.UUID, str],
) -> ClusteringOutcome:
    """Group geography units by their food share vectors, choosing k by silhouette."""
    units = sorted(matrix)
    if len(units) < MIN_CLUSTERS + 1:
        # Too few units to cluster meaningfully: everything lands in one named group.
        label = "All units"
        return ClusteringOutcome(
            labels={unit: label for unit in units},
            cluster_count=1,
            silhouette=None,
            summaries={
                label: {
                    "units": units,
                    "note": "Too few geography units for clustering",
                    "top_foods": [],
                    "missing_foods": [],
                }
            },
        )

    food_ids = sorted({food for values in matrix.values() for food in values})
    index = {food_id: position for position, food_id in enumerate(food_ids)}
    data = np.zeros((len(units), len(food_ids)), dtype=float)
    for row, unit in enumerate(units):
        for food_id, share_value in matrix[unit].items():
            data[row, index[food_id]] = share_value

    best: tuple[float, int, np.ndarray] | None = None
    upper = min(MAX_CLUSTERS, len(units) - 1)
    # Duplicate share vectors are common in census data, so fewer distinct clusters than
    # requested is expected and not a problem worth warning about.
    distinct_rows = len({tuple(row) for row in data})
    for k in range(MIN_CLUSTERS, min(upper, distinct_rows - 1) + 1):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", ConvergenceWarning)
                model = KMeans(n_clusters=k, n_init=10, random_state=42)
                labels = model.fit_predict(data)
        except ValueError:
            continue
        if len(set(labels)) < 2:
            continue
        score = float(silhouette_score(data, labels))
        if best is None or score > best[0]:
            best = (score, k, labels)

    if best is None:
        label = "All units"
        return ClusteringOutcome(
            labels={unit: label for unit in units},
            cluster_count=1,
            silhouette=None,
            summaries={label: {"units": units, "top_foods": [], "missing_foods": []}},
        )

    score, k, labels = best
    grouped: dict[int, list[str]] = defaultdict(list)
    for unit, label in zip(units, labels, strict=True):
        grouped[int(label)].append(unit)

    means = {
        cluster: data[[position for position, value in enumerate(labels) if value == cluster]].mean(
            axis=0
        )
        for cluster in grouped
    }
    overall = data.mean(axis=0)

    label_names: dict[int, str] = {}
    summaries: dict[str, dict[str, Any]] = {}
    for cluster, members in grouped.items():
        name = f"Cluster {cluster + 1}"
        label_names[cluster] = name
        deltas = means[cluster] - overall
        top_positions = np.argsort(-deltas)[:3]
        missing_positions = np.argsort(deltas)[:3]
        summaries[name] = {
            "units": sorted(members),
            "size": len(members),
            "top_foods": [
                {
                    "food": names.get(food_ids[position], str(food_ids[position])),
                    "share": round(float(means[cluster][position]), 4),
                }
                for position in top_positions
                if means[cluster][position] > 0
            ],
            "missing_foods": [
                {
                    "food": names.get(food_ids[position], str(food_ids[position])),
                    "share": round(float(means[cluster][position]), 4),
                }
                for position in missing_positions
            ],
        }

    return ClusteringOutcome(
        labels={unit: label_names[int(labels[position])] for position, unit in enumerate(units)},
        cluster_count=k,
        silhouette=round(score, 4),
        summaries=summaries,
    )


def _fingerprint(payload: dict[str, Any]) -> str:
    import hashlib

    encoded = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def run_ranking(
    db: Session,
    survey: Survey,
    *,
    weights: dict[str, float] | None = None,
    nutrition_needs: dict[str, float] | None = None,
    created_by_id: uuid.UUID | None = None,
    provinces: list[str] | None = None,
) -> RankingResult:
    """Compute one reproducible ranking run and store every ranked item."""
    resolved_weights = resolve_weights(db, weights)
    resolved_needs = resolve_nutrition_needs(db, nutrition_needs)

    run = AiRankingRun(
        survey_id=survey.id,
        status=RankingStatus.RUNNING,
        weights=resolved_weights,
        nutrition_needs=resolved_needs,
        created_by_id=created_by_id,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    try:
        result = _compute(db, run, survey, resolved_weights, resolved_needs, provinces)
    except Exception as exc:  # noqa: BLE001 - the run row must record the failure
        logger.exception("Ranking run %s failed", run.id)
        db.rollback()
        stored = db.get(AiRankingRun, run.id)
        if stored is not None:
            stored.status = RankingStatus.FAILED
            stored.warnings = [f"{type(exc).__name__}: {exc}"[:300]]
            db.commit()
        raise
    return result


def _compute(
    db: Session,
    run: AiRankingRun,
    survey: Survey,
    weights: dict[str, float],
    needs: dict[str, float],
    provinces: list[str] | None,
) -> RankingResult:
    foods = list(db.scalars(select(FoodItem).order_by(FoodItem.source_variable)))
    groups = {food.id: food.group for food in foods}
    names = {food.id: food.standard_name for food in foods}

    national = _share_map(db, survey, GeoLevel.NATIONAL)
    province_shares = _share_map(db, survey, GeoLevel.PROVINCE)
    district_shares = _share_map(db, survey, GeoLevel.DISTRICT)

    warnings: list[str] = []
    amounts = nutrition_module.nutrition_by_food(db)
    if not amounts:
        warnings.append(
            "Nutrition data is needed: the ranking currently uses the under-farming gap only"
        )

    cluster_outcome = cluster_units(province_shares, names)
    for unit, label in cluster_outcome.labels.items():
        db.add(
            ProvinceCluster(
                run_id=run.id,
                level=GeoLevel.PROVINCE.value,
                unit=unit,
                cluster_label=label,
                summary=cluster_outcome.summaries.get(label, {}),
            )
        )

    district_clusters = cluster_units(district_shares, names)
    for unit, label in district_clusters.labels.items():
        db.add(
            ProvinceCluster(
                run_id=run.id,
                level=GeoLevel.DISTRICT.value,
                unit=unit,
                cluster_label=label,
                summary=district_clusters.summaries.get(label, {}),
            )
        )

    selected_provinces = provinces or sorted(province_shares)
    scope: list[str | None] = [None, *selected_provinces]

    items_written = 0
    top_national: list[dict[str, Any]] = []

    for province in scope:
        shares = national.get(None, {}) if province is None else province_shares.get(province, {})
        if not shares:
            continue
        scores = gap_scores(shares, groups)
        ranked: list[tuple[float, AiRankingItem]] = []

        for food in foods:
            if food.id not in shares:
                continue
            share_value = shares[food.id]
            gap = scores.get(food.id, 0.0)
            amount = amounts.get(food.id)
            value = nutrition_module.nutrition_value(amount, needs) if amount else None
            need = _nutrition_need(amount, needs)
            feasibility = _feasibility(food)

            if value is None:
                # Gap only mode: the score is scaled so the two are still comparable.
                total = (weights["gap"] * gap + weights["feasibility"] * feasibility) / max(
                    weights["gap"] + weights["feasibility"], 1e-9
                )
                score_basis = "gap_only"
            else:
                total = (
                    weights["nutrition_value"] * value
                    + weights["nutrition_need"] * (need or 0.0)
                    + weights["feasibility"] * feasibility
                    + weights["gap"] * gap
                )
                score_basis = "full"

            cluster_label = None
            if province is not None:
                cluster_label = cluster_outcome.labels.get(province)

            item = AiRankingItem(
                run_id=run.id,
                food_item_id=food.id,
                province=province,
                rank=0,
                gap_score=round(gap, 4),
                nutrition_value=None if value is None else round(value, 4),
                nutrition_need=None if need is None else round(need, 4),
                feasibility=round(feasibility, 4),
                total_score=round(max(0.0, min(1.0, total)), 4),
                cluster_label=cluster_label,
                pinned=food.source_variable.lower() == MUSHROOM_VARIABLE,
                reason_fields={
                    "weighted_share": round(share_value, 4),
                    "food_group": food.group.value,
                    "cluster": cluster_label,
                    "feasibility": {
                        "budget_fit": food.budget_fit.value,
                        "land_need": food.land_need.value,
                        "season_fit": food.season_fit.value,
                    },
                    "top_nutrient": _top_nutrient(amount),
                    "score_basis": score_basis,
                    "pinned_reason": (
                        MUSHROOM_PIN_REASON
                        if food.source_variable.lower() == MUSHROOM_VARIABLE
                        else None
                    ),
                },
                explanation=_explain(
                    food=food,
                    province=province,
                    share_value=share_value,
                    gap=gap,
                    value=value,
                    need=need,
                    cluster_label=cluster_label,
                    top_nutrient=_top_nutrient(amount),
                ),
            )
            ranked.append((float(item.total_score or 0), item))

        # Pinned foods sort first, then by score, then by name for a stable order.
        ranked.sort(key=lambda pair: (not pair[1].pinned, -pair[0], str(pair[1].food_item_id)))
        for position, (_, item) in enumerate(ranked, start=1):
            item.rank = position
            db.add(item)
            items_written += 1

        if province is None:
            top_national = [
                {
                    "food_item_id": str(item.food_item_id),
                    "standard_name": names.get(item.food_item_id, ""),
                    "score": float(item.total_score or 0),
                    "pinned": item.pinned,
                    "explanation": item.explanation,
                }
                for _, item in ranked[:5]
            ]

    run.status = RankingStatus.COMPLETED
    run.nutrition_needs = needs
    run.score_basis = "full" if amounts else "gap_only"
    run.cluster_count = cluster_outcome.cluster_count
    run.silhouette = cluster_outcome.silhouette
    run.warnings = warnings
    run.completed_at = datetime.now(UTC)
    run.fingerprint = _fingerprint(
        {
            "survey": str(survey.id),
            "weights": weights,
            "needs": needs,
            "clusters": cluster_outcome.cluster_count,
            "items": items_written,
        }
    )
    db.commit()

    return RankingResult(
        run_id=run.id,
        score_basis=run.score_basis,
        cluster_count=run.cluster_count,
        silhouette=cluster_outcome.silhouette,
        items_written=items_written,
        warnings=warnings,
        top_under_farmed=top_national,
    )


def _nutrition_need(amounts: dict[str, float] | None, needs: dict[str, float]) -> float | None:
    """How much this food contributes to the nutrients the programme cares about."""
    if not amounts:
        return None
    relevant = [
        nutrient for nutrient in needs if amounts.get(nutrient) is not None and needs[nutrient] > 0
    ]
    if not relevant:
        return None
    coverage = len(relevant) / len(needs) if needs else 0.0
    density = sum(
        min(amounts[nutrient] / nutrition_module.REFERENCE_DAILY_AMOUNT.get(nutrient, 10.0), 1.0)
        * needs[nutrient]
        for nutrient in relevant
    ) / sum(needs[nutrient] for nutrient in relevant)
    return round(min(1.0, 0.5 * coverage + 0.5 * density), 4)


def _feasibility(food: FoodItem) -> float:
    scores = [
        FEASIBILITY_SCORE.get(food.budget_fit, 0.5),
        FEASIBILITY_SCORE.get(food.land_need, 0.5),
        FEASIBILITY_SCORE.get(food.season_fit, 0.5),
    ]
    return round(sum(scores) / len(scores), 4)


def _top_nutrient(amounts: dict[str, float] | None) -> str | None:
    if not amounts:
        return None
    nutrient = max(
        amounts,
        key=lambda name: amounts[name] / nutrition_module.REFERENCE_DAILY_AMOUNT.get(name, 10.0),
    )
    return nutrient


def _explain(
    *,
    food: FoodItem,
    province: str | None,
    share_value: float,
    gap: float,
    value: float | None,
    need: float | None,
    cluster_label: str | None,
    top_nutrient: str | None,
) -> str:
    """A short, factual sentence. No health claims, no recommendations to eat."""
    place = province or "Rwanda"
    percent = round(share_value * 100, 1)
    parts = [f"{percent}% of households in {place} grow or rear {food.standard_name}."]
    if gap >= 0.6:
        parts.append("It is grown far less than comparable foods in its group.")
    elif gap >= 0.3:
        parts.append("It is grown less than comparable foods in its group.")
    if cluster_label:
        parts.append(f"Province cluster: {cluster_label}.")
    if value is None:
        parts.append("Nutrition data is not available yet, so the score uses the gap only.")
    elif top_nutrient:
        parts.append(
            f"Highest nutrient relative to a daily need: {top_nutrient.replace('_', ' ')}."
        )
    if need is not None:
        parts.append("Measured against the nutrition priorities set by the programme.")
    return " ".join(parts)


def latest_run(db: Session, survey_id: uuid.UUID | None = None) -> AiRankingRun | None:
    query = select(AiRankingRun).where(AiRankingRun.status == RankingStatus.COMPLETED)
    if survey_id is not None:
        query = query.where(AiRankingRun.survey_id == survey_id)
    return db.scalar(query.order_by(AiRankingRun.created_at.desc()).limit(1))


def clear_runs(db: Session, survey_id: uuid.UUID) -> None:
    db.execute(delete(AiRankingRun).where(AiRankingRun.survey_id == survey_id))
    db.commit()
