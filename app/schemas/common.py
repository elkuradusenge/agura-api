"""Shared response schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    code: str
    translation_key: str
    message: str
    details: Any | None = None


class ErrorEnvelope(BaseModel):
    error: ErrorDetail


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    environment: str


class ReadinessResponse(BaseModel):
    status: str
    database: str


class MessageResponse(BaseModel):
    message: str = Field(default="ok")
