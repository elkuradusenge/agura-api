"""Authentication request and response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.enums import Language
from app.schemas.admin import AdminWithPermissions
from app.schemas.user import UserRead


class RegisterRequest(BaseModel):
    phone: str = Field(min_length=1, max_length=20, examples=["0788123456"])
    password: str = Field(min_length=1, max_length=128)
    full_name: str | None = Field(default=None, max_length=120)
    language: Language = Language.RW


class LoginRequest(BaseModel):
    phone: str = Field(min_length=1, max_length=20)
    password: str = Field(min_length=1, max_length=128)


class AdminLoginRequest(BaseModel):
    identifier: str = Field(
        min_length=3, max_length=255, description="Administrator email or phone number"
    )
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=10, max_length=512)


class LogoutRequest(BaseModel):
    refresh_token: str | None = Field(default=None, max_length=512)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class UserAuthResponse(TokenResponse):
    account: UserRead


class AdminAuthResponse(TokenResponse):
    account: AdminWithPermissions


class PasswordResetRequest(BaseModel):
    phone: str = Field(min_length=1, max_length=20)


class PasswordResetRequestResponse(BaseModel):
    message: str = "If the number is registered a reset code has been sent"
    translation_key: str = "auth.password_reset.sent"
    # Only present outside production so the flow can be tested without SMS.
    debug_code: str | None = None


class PasswordResetConfirmRequest(BaseModel):
    phone: str = Field(min_length=1, max_length=20)
    code: str = Field(min_length=4, max_length=8)
    new_password: str = Field(min_length=1, max_length=128)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=1, max_length=128)


class AccountDeletedResponse(BaseModel):
    message: str = "Account deleted"
    deleted_at: datetime
