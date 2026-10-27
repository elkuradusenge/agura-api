"""The /ws endpoint: JWT handshake, two channels, heartbeat and catch-up."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.errors import ApiError
from app.core.security import decode_access_token
from app.db import get_session_factory
from app.models.admin import Admin
from app.models.enums import AdminStatus, SubjectType, UserStatus, WsChannel
from app.models.user import User
from app.ws import broadcaster

logger = logging.getLogger("agura.ws")

router = APIRouter(tags=["ws"])

HEARTBEAT_SECONDS = 25


def _authenticate(db: Session, token: str) -> tuple[WsChannel, uuid.UUID, str]:
    payload = decode_access_token(token)
    subject = payload.get("act")
    try:
        subject_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise ApiError(
            401, "auth.invalid_token", "errors.auth.invalid_token", "Invalid token"
        ) from exc

    if subject == SubjectType.ADMIN.value:
        admin = db.get(Admin, subject_id)
        if admin is None or admin.status != AdminStatus.ACTIVE:
            raise ApiError(401, "auth.invalid_token", "errors.auth.invalid_token", "Invalid token")
        return WsChannel.ADMIN, admin.id, admin.role.value

    if subject == SubjectType.USER.value:
        user = db.get(User, subject_id)
        if user is None or user.status != UserStatus.ACTIVE:
            raise ApiError(401, "auth.invalid_token", "errors.auth.invalid_token", "Invalid token")
        return WsChannel.USER, user.id, "user"

    raise ApiError(401, "auth.invalid_token", "errors.auth.invalid_token", "Invalid token")


async def _read_token(websocket: WebSocket, token_from_query: str | None) -> tuple[str, str | None]:
    """The token arrives in the first message, or in the query for wss behind a proxy."""
    if token_from_query:
        return token_from_query, None
    timeout = get_settings().ws_auth_timeout_seconds
    raw = await asyncio.wait_for(websocket.receive_text(), timeout=timeout)
    try:
        message = json.loads(raw)
    except json.JSONDecodeError:
        return raw.strip(), None
    if isinstance(message, dict):
        return str(message.get("token", "")), message.get("since")
    return str(message), None


@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    token: str | None = Query(default=None),
    since: str | None = Query(default=None),
) -> None:
    await websocket.accept()

    session = get_session_factory()()
    channel: WsChannel | None = None
    subject_id: uuid.UUID | None = None
    heartbeat: asyncio.Task | None = None

    try:
        try:
            raw_token, message_since = await _read_token(websocket, token)
            channel, subject_id, role = _authenticate(session, raw_token)
        except (ApiError, TimeoutError, WebSocketDisconnect) as exc:
            code = (
                "auth.not_authenticated"
                if isinstance(exc, TimeoutError)
                else getattr(exc, "code", "auth.invalid_token")
            )
            await websocket.send_json(
                {
                    "type": "error",
                    "id": str(uuid.uuid4()),
                    "timestamp": None,
                    "payload": {
                        "code": code,
                        "translation_key": f"errors.{code}",
                    },
                }
            )
            await websocket.close(code=1008)
            return

        await websocket.send_json(
            {
                "type": "connection.ready",
                "id": str(uuid.uuid4()),
                "timestamp": None,
                "payload": {"channel": channel.value, "role": role},
            }
        )

        await broadcaster.registry.add(channel, websocket, user_id=subject_id)

        # Catch up on anything that happened while the client was away.
        cursor = _parse_since(message_since if token is None else since)
        missed = broadcaster.catch_up(session, channel=channel, user_id=subject_id, since=cursor)
        for event in missed:
            await websocket.send_json(broadcaster.envelope(event))
        await websocket.send_json(
            {
                "type": "catchup.completed",
                "id": str(uuid.uuid4()),
                "timestamp": None,
                "payload": {"count": len(missed)},
            }
        )

        heartbeat = asyncio.create_task(_heartbeat(websocket))

        while True:
            message = await websocket.receive_text()
            if message == "pong" or message == '"pong"':
                continue
            try:
                parsed = json.loads(message)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and parsed.get("type") == "ping":
                await websocket.send_json(
                    {
                        "type": "pong",
                        "id": str(uuid.uuid4()),
                        "timestamp": None,
                        "payload": {},
                    }
                )
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001 - never leak a stack trace to the socket
        logger.exception("WebSocket failure")
    finally:
        if heartbeat is not None:
            heartbeat.cancel()
        if channel is not None:
            await broadcaster.registry.remove(channel, websocket, user_id=subject_id)
        session.close()


async def _heartbeat(websocket: WebSocket) -> None:
    """Server side ping so idle connections are not silently dropped by a proxy."""
    try:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            await websocket.send_json(
                {
                    "type": "ping",
                    "id": str(uuid.uuid4()),
                    "timestamp": None,
                    "payload": {"interval_seconds": HEARTBEAT_SECONDS},
                }
            )
    except (asyncio.CancelledError, RuntimeError):
        return
    except Exception:  # noqa: BLE001
        return


def _parse_since(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
