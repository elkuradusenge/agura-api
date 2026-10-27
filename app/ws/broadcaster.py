"""Publishing WebSocket events.

Two things must be true for the interface to be reliable: an event is persisted
before anyone receives it, and a reconnecting client can ask for everything it
missed. The broadcaster therefore writes to `ws_events` first and only then pushes
to live connections. In-process delivery is enough because the platform runs one API
instance; the persisted log is what makes catch-up work.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import WsChannel
from app.models.ws_event import WsEvent

logger = logging.getLogger("agura.ws")


class ConnectionRegistry:
    """Live connections per channel, with user specific routing for the user channel."""

    def __init__(self) -> None:
        self._admin: set[Any] = set()
        self._users: dict[uuid.UUID, set[Any]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def add(
        self, channel: WsChannel, connection: Any, *, user_id: uuid.UUID | None = None
    ) -> None:
        async with self._lock:
            if channel is WsChannel.ADMIN:
                self._admin.add(connection)
            elif user_id is not None:
                self._users[user_id].add(connection)

    async def remove(
        self, channel: WsChannel, connection: Any, *, user_id: uuid.UUID | None = None
    ) -> None:
        async with self._lock:
            if channel is WsChannel.ADMIN:
                self._admin.discard(connection)
            elif user_id is not None:
                connections = self._users.get(user_id)
                if connections is not None:
                    connections.discard(connection)
                    if not connections:
                        self._users.pop(user_id, None)

    async def targets(self, channel: WsChannel, recipient_id: uuid.UUID | None) -> list[Any]:
        async with self._lock:
            if channel is WsChannel.ADMIN:
                return list(self._admin)
            if recipient_id is None:
                return [connection for group in self._users.values() for connection in group]
            return list(self._users.get(recipient_id, ()))

    def counts(self) -> dict[str, int]:
        return {
            "admin": len(self._admin),
            "user": sum(len(group) for group in self._users.values()),
        }

    def clear(self) -> None:
        self._admin.clear()
        self._users.clear()


registry = ConnectionRegistry()

# The event loop that owns the live connections. Job workers run in their own threads,
# so they must hand the send back to this loop instead of trying to await here.
_server_loop: asyncio.AbstractEventLoop | None = None


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _server_loop
    _server_loop = loop


def bound_loop() -> asyncio.AbstractEventLoop | None:
    return _server_loop


def envelope(event: WsEvent) -> dict[str, Any]:
    """The wire format: type, id, timestamp and payload."""
    return {
        "type": event.type,
        "id": str(event.id),
        "timestamp": event.created_at.isoformat() if event.created_at else None,
        "payload": event.payload or {},
    }


def record(
    db: Session,
    *,
    channel: WsChannel,
    event_type: str,
    payload: dict[str, Any] | None = None,
    recipient_id: uuid.UUID | None = None,
) -> WsEvent:
    """Persist one event. Call this inside the action that caused it."""
    event = WsEvent(
        channel=channel,
        type=event_type,
        recipient_id=recipient_id,
        payload=payload or {},
    )
    db.add(event)
    db.flush()
    return event


def publish(
    db: Session,
    *,
    channel: WsChannel,
    event_type: str,
    payload: dict[str, Any] | None = None,
    recipient_id: uuid.UUID | None = None,
) -> WsEvent:
    """Persist and push. Safe to call from a background thread or a request handler."""
    event = record(
        db,
        channel=channel,
        event_type=event_type,
        payload=payload,
        recipient_id=recipient_id,
    )
    db.commit()
    db.refresh(event)
    dispatch(event)
    return event


def dispatch(event: WsEvent) -> None:
    """Deliver on whichever loop owns the connections, from any thread."""
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None

    if running is not None and running is _server_loop:
        running.create_task(_send(event))
        return

    loop = _server_loop
    if loop is None or loop.is_closed():
        # Nothing is bound, for example in a bare unit test. The event is already
        # persisted, so a client still receives it through catch up.
        logger.debug("No bound loop for %s, stored only", event.type)
        return
    asyncio.run_coroutine_threadsafe(_send(event), loop)


async def _send(event: WsEvent) -> None:
    message = envelope(event)
    connections = await registry.targets(event.channel, event.recipient_id)
    for connection in connections:
        try:
            await connection.send_json(message)
        except Exception:  # noqa: BLE001 - a dead socket must not stop the others
            logger.debug("Dropping a connection that failed to receive %s", event.type)
            await registry.remove(event.channel, connection, user_id=event.recipient_id)


def catch_up(
    db: Session,
    *,
    channel: WsChannel,
    user_id: uuid.UUID | None = None,
    since: datetime | None = None,
    limit: int = 200,
) -> list[WsEvent]:
    """Everything a reconnecting client missed, oldest first."""
    query = select(WsEvent).where(WsEvent.channel == channel)
    if channel is WsChannel.USER:
        if user_id is None:
            return []
        # Either addressed to this user or broadcast to everyone.
        query = query.where((WsEvent.recipient_id == user_id) | (WsEvent.recipient_id.is_(None)))
    if since is not None:
        query = query.where(WsEvent.created_at > since)
    rows = db.scalars(query.order_by(WsEvent.created_at.desc()).limit(limit)).all()
    return list(reversed(rows))


def notify_admins(db: Session, event_type: str, payload: dict[str, Any] | None = None) -> WsEvent:
    return publish(db, channel=WsChannel.ADMIN, event_type=event_type, payload=payload)


def notify_user(
    db: Session,
    user_id: uuid.UUID,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> WsEvent:
    return publish(
        db, channel=WsChannel.USER, event_type=event_type, payload=payload, recipient_id=user_id
    )


def notify_all_users(
    db: Session, event_type: str, payload: dict[str, Any] | None = None
) -> WsEvent:
    return publish(db, channel=WsChannel.USER, event_type=event_type, payload=payload)
