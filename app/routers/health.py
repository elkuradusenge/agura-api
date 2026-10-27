"""Liveness and readiness endpoints."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from app import __version__
from app.config import get_settings
from app.core.deps import DbSession
from app.schemas.common import HealthResponse, ReadinessResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        version=__version__,
        environment=settings.environment,
    )


@router.get("/health/ready", response_model=ReadinessResponse, summary="Readiness probe")
def readiness(db: DbSession) -> ReadinessResponse:
    db.execute(text("SELECT 1"))
    return ReadinessResponse(status="ok", database="ok")
