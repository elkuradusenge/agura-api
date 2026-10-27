"""Authentication endpoints for farmers and administrators."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from app.config import get_settings
from app.core.deps import DbSession, client_ip, user_agent
from app.core.permissions import permissions_for
from app.core.rate_limit import check_rate_limit
from app.schemas.admin import AdminWithPermissions
from app.schemas.auth import (
    AdminAuthResponse,
    AdminLoginRequest,
    LoginRequest,
    LogoutRequest,
    PasswordResetConfirmRequest,
    PasswordResetRequest,
    PasswordResetRequestResponse,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserAuthResponse,
)
from app.schemas.common import MessageResponse
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


def _admin_payload(admin) -> AdminWithPermissions:
    payload = AdminWithPermissions.model_validate(admin)
    payload.permissions = sorted(permissions_for(admin.role), key=lambda item: item.value)
    return payload


@router.post(
    "/register",
    response_model=UserAuthResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a farmer account with a phone number and password",
)
def register(payload: RegisterRequest, request: Request, db: DbSession) -> UserAuthResponse:
    check_rate_limit("auth.register", client_ip(request))
    user, access_token, refresh_token, expires_in = auth_service.register_user(db, payload)
    return UserAuthResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
        account=user,
    )


@router.post("/login", response_model=UserAuthResponse, summary="Farmer sign in")
def login(payload: LoginRequest, request: Request, db: DbSession) -> UserAuthResponse:
    check_rate_limit("auth.login", f"{client_ip(request)}:{payload.phone}")
    user, access_token, refresh_token, expires_in = auth_service.authenticate_user(
        db, payload.phone, payload.password
    )
    return UserAuthResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
        account=user,
    )


@router.post("/admin/login", response_model=AdminAuthResponse, summary="Administrator sign in")
def admin_login(payload: AdminLoginRequest, request: Request, db: DbSession) -> AdminAuthResponse:
    check_rate_limit("auth.admin_login", f"{client_ip(request)}:{payload.identifier}")
    admin, access_token, refresh_token, expires_in = auth_service.authenticate_admin(
        db, payload.identifier, payload.password
    )
    return AdminAuthResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
        account=_admin_payload(admin),
    )


@router.post("/refresh", response_model=TokenResponse, summary="Exchange a refresh token")
def refresh(payload: RefreshRequest, request: Request, db: DbSession) -> TokenResponse:
    _, _, access_token, refresh_token, expires_in = auth_service.rotate_refresh_token(
        db, payload.refresh_token, ip_address=client_ip(request), user_agent=user_agent(request)
    )
    return TokenResponse(
        access_token=access_token, refresh_token=refresh_token, expires_in=expires_in
    )


@router.post(
    "/logout",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Revoke a refresh token",
)
def logout(payload: LogoutRequest, db: DbSession) -> MessageResponse:
    if payload.refresh_token:
        auth_service.revoke_refresh_token(db, payload.refresh_token)
    return MessageResponse(message="signed_out")


@router.post(
    "/password-reset/request",
    response_model=PasswordResetRequestResponse,
    summary="Request a password reset code by SMS",
)
def request_password_reset(
    payload: PasswordResetRequest, request: Request, db: DbSession, response: Response
) -> PasswordResetRequestResponse:
    settings = get_settings()
    check_rate_limit("auth.password_reset", f"{client_ip(request)}:{payload.phone}")
    code = auth_service.request_password_reset(db, payload.phone)
    response.status_code = status.HTTP_202_ACCEPTED
    return PasswordResetRequestResponse(
        debug_code=None if settings.is_production else code,
    )


@router.post(
    "/password-reset/confirm",
    response_model=MessageResponse,
    summary="Set a new password using the SMS code",
)
def confirm_password_reset(
    payload: PasswordResetConfirmRequest, request: Request, db: DbSession
) -> MessageResponse:
    check_rate_limit("auth.password_reset_confirm", client_ip(request))
    auth_service.confirm_password_reset(db, payload.phone, payload.code, payload.new_password)
    return MessageResponse(message="password_reset_done")
