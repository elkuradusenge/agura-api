"""Persisted WebSocket events so a reconnect can catch up on everything missed."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin, value_enum
from app.models.enums import WsChannel


class WsEvent(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "ws_events"
    __table_args__ = (
        Index("ix_ws_events_channel_created", "channel", "created_at"),
        Index("ix_ws_events_recipient_created", "recipient_id", "created_at"),
    )

    channel: Mapped[WsChannel] = mapped_column(
        value_enum(WsChannel, "ws_channel", 16), nullable=False
    )
    type: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    # Null means the event goes to every connection on the channel.
    recipient_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )


class WsEventCursor(Base, UUIDPrimaryKeyMixin):
    """Kept so retention can be cleaned without losing a client's place."""

    __tablename__ = "ws_event_cursors"

    subject_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, unique=True)
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
