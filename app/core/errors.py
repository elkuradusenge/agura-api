"""Consistent API error format: an error code plus a translation key for the clients."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    """Raised anywhere in the app to produce the standard error envelope."""

    def __init__(
        self,
        status_code: int,
        code: str,
        translation_key: str,
        message: str,
        details: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.translation_key = translation_key
        self.message = message
        self.details = details

    def to_response(self) -> JSONResponse:
        body: dict[str, Any] = {
            "error": {
                "code": self.code,
                "translation_key": self.translation_key,
                "message": self.message,
            }
        }
        if self.details is not None:
            body["error"]["details"] = self.details
        return JSONResponse(status_code=self.status_code, content=body)


def not_authenticated(message: str = "Authentication is required") -> ApiError:
    return ApiError(401, "auth.not_authenticated", "errors.auth.not_authenticated", message)


def invalid_credentials(message: str = "Phone number or password is incorrect") -> ApiError:
    return ApiError(401, "auth.invalid_credentials", "errors.auth.invalid_credentials", message)


def invalid_token(message: str = "The session is no longer valid") -> ApiError:
    return ApiError(401, "auth.invalid_token", "errors.auth.invalid_token", message)


def forbidden(message: str = "You do not have permission to do this") -> ApiError:
    return ApiError(403, "auth.forbidden", "errors.auth.forbidden", message)


def not_found(entity: str = "resource") -> ApiError:
    return ApiError(404, f"{entity}.not_found", f"errors.{entity}.not_found", "Not found")


def conflict(code: str, translation_key: str, message: str) -> ApiError:
    return ApiError(409, code, translation_key, message)


def unprocessable(code: str, translation_key: str, message: str, details: Any = None) -> ApiError:
    return ApiError(422, code, translation_key, message, details)


def rate_limited(retry_after_seconds: int) -> ApiError:
    return ApiError(
        429,
        "rate_limited",
        "errors.rate_limited",
        "Too many attempts. Try again shortly.",
        {"retry_after_seconds": retry_after_seconds},
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _handle_api_error(_: Request, exc: ApiError) -> JSONResponse:
        return exc.to_response()

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"field": ".".join(str(part) for part in error["loc"][1:]), "reason": error["msg"]}
            for error in exc.errors()
        ]
        return ApiError(
            422,
            "validation_error",
            "errors.validation",
            "Request validation failed",
            details,
        ).to_response()

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            return not_found().to_response()
        return ApiError(
            exc.status_code,
            "http_error",
            "errors.http",
            str(exc.detail),
        ).to_response()
