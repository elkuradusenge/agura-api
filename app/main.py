"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import SQLAlchemyError

from app import __version__
from app.config import get_settings
from app.core.errors import register_error_handlers
from app.db import get_session_factory
from app.routers import (
    admin_ai,
    admin_analysis,
    admin_audit,
    admin_products,
    admin_support,
    admin_surveys,
    admin_users,
    auth,
    farmer,
    health,
    me,
)
from app.services.auth_service import ensure_bootstrap_admin
from app.ws.broadcaster import bind_loop as broadcaster_bind
from app.ws.router import router as ws_router

logger = logging.getLogger("agura.app")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper())
    session = get_session_factory()()
    try:
        ensure_bootstrap_admin(session)
    except SQLAlchemyError as exc:
        logger.warning("Skipping administrator bootstrap, database not ready: %s", exc)
    finally:
        session.close()
    broadcaster_bind(asyncio.get_running_loop())
    logger.info("AGUra API %s starting in %s", __version__, settings.environment)
    yield
    logger.info("AGUra API stopping")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description="Census analysis and crop adoption platform for Rwanda.",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_error_handlers(app)

    routers = (
        health.router,
        auth.router,
        me.router,
        admin_users.router,
        admin_audit.router,
        admin_surveys.router,
        admin_analysis.router,
        admin_ai.router,
        admin_products.router,
        admin_support.router,
        farmer.router,
    )
    app.include_router(ws_router)

    for router in routers:
        app.include_router(router, prefix=settings.api_prefix)

    @app.get("/", tags=["health"], summary="Service banner")
    def root() -> dict[str, str]:
        return {
            "service": settings.app_name,
            "version": __version__,
            "docs": "/docs",
            "api": settings.api_prefix,
        }

    return app


app = create_app()
