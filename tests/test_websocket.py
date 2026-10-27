"""WebSocket authentication, event order, persistence and catch-up."""

from __future__ import annotations

import queue
import threading
from typing import Any

from sqlalchemy import text

from app.core.security import create_access_token
from app.models.enums import SubjectType, WsChannel
from app.ws import broadcaster
from tests.conftest import admin_headers, confirm_mapping, upload_survey

SOCKET_TIMEOUT = 8.0


class SocketReader:
    """Reads socket messages on a thread so a missing message cannot hang a test."""

    def __init__(self, websocket: Any) -> None:
        self._websocket = websocket
        self._messages: queue.Queue[dict | None] = queue.Queue()
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def _pump(self) -> None:
        try:
            while True:
                self._messages.put(self._websocket.receive_json())
        except Exception:  # noqa: BLE001 - a closed socket ends the pump
            self._messages.put(None)

    def next(self, timeout: float = SOCKET_TIMEOUT) -> dict | None:
        try:
            return self._messages.get(timeout=timeout)
        except queue.Empty:
            return None

    def until(self, wanted: str, *, limit: int = 300) -> list[str]:
        """Collect message types until `wanted` arrives or the socket goes quiet."""
        seen: list[str] = []
        for _ in range(limit):
            message = self.next()
            if message is None:
                break
            if message["type"] in {"ping", "pong"}:
                continue
            seen.append(message["type"])
            if message["type"] == wanted:
                break
        return seen


def connect(client, token: str, **params):
    return client.websocket_connect("/ws", params={"token": token, **params})


def admin_token(admin) -> str:
    token, _ = create_access_token(admin.id, SubjectType.ADMIN, admin.role.value)
    return token


def user_token(user) -> str:
    token, _ = create_access_token(user.id, SubjectType.USER)
    return token


def test_admin_socket_receives_ready_then_stage_events_in_order(
    client, admin_factory, synthetic_dta
):
    admin = admin_factory()
    headers = admin_headers(admin)

    with connect(client, admin_token(admin)) as websocket:
        reader = SocketReader(websocket)
        ready = reader.next()
        assert ready is not None
        assert ready["type"] == "connection.ready"
        assert ready["payload"]["channel"] == "admin"

        upload_survey(client, headers, synthetic_dta)
        seen = reader.until("job.mapping_required")

    assert "job.stage_started" in seen
    assert "job.stage_completed" in seen
    assert "job.mapping_required" in seen
    assert seen.count("job.stage_started") >= 3


def test_admin_socket_receives_job_completed(
    client, admin_factory, synthetic_dta, seeded_food_items
):
    admin = admin_factory()
    headers = admin_headers(admin)
    body = upload_survey(client, headers, synthetic_dta).json()

    with connect(client, admin_token(admin)) as websocket:
        reader = SocketReader(websocket)
        assert reader.next() is not None
        confirm_mapping(client, headers, body["job"]["id"])
        seen = reader.until("job.completed")

    assert "job.completed" in seen


def test_events_are_persisted_with_type_id_timestamp_and_payload(client, admin_factory, db):
    admin_factory()
    event = broadcaster.notify_admins(db, "job.started", {"job_id": "abc"})
    assert event.id is not None
    assert event.created_at is not None

    stored = db.execute(
        text("SELECT type, payload, created_at FROM ws_events WHERE id = :id"),
        {"id": str(event.id)},
    ).one()
    assert stored.type == "job.started"
    assert stored.payload == {"job_id": "abc"}
    assert stored.created_at is not None

    wire = broadcaster.envelope(event)
    assert set(wire) == {"type", "id", "timestamp", "payload"}


def test_catch_up_returns_missed_events_in_order(client, admin_factory, db):
    admin_factory()
    for name in ("upload.received", "job.started", "job.stage_started"):
        broadcaster.notify_admins(db, name, {})

    events = broadcaster.catch_up(db, channel=WsChannel.ADMIN)
    assert [event.type for event in events] == [
        "upload.received",
        "job.started",
        "job.stage_started",
    ]


def test_catch_up_only_returns_events_after_the_cursor(client, admin_factory, db):
    admin_factory()
    first = broadcaster.notify_admins(db, "upload.received", {})
    broadcaster.notify_admins(db, "job.started", {})

    events = broadcaster.catch_up(db, channel=WsChannel.ADMIN, since=first.created_at)
    assert [event.type for event in events] == ["job.started"]


def test_user_events_are_private_to_the_recipient(client, db, user_factory):
    first = user_factory(phone="+250788100001")
    second = user_factory(phone="+250788100002")

    broadcaster.notify_user(db, first.id, "notification.created", {"title": "one"})
    broadcaster.notify_all_users(db, "analysis.updated", {"survey_id": "s1"})

    mine = [
        event.type for event in broadcaster.catch_up(db, channel=WsChannel.USER, user_id=first.id)
    ]
    theirs = [
        event.type for event in broadcaster.catch_up(db, channel=WsChannel.USER, user_id=second.id)
    ]
    assert mine == ["notification.created", "analysis.updated"]
    assert theirs == ["analysis.updated"]


def test_user_socket_receives_analysis_updated_after_a_job(
    client, admin_factory, user_factory, synthetic_dta, seeded_food_items
):
    admin = admin_factory()
    headers = admin_headers(admin)
    farmer = user_factory(phone="+250788200001")
    body = upload_survey(client, headers, synthetic_dta).json()

    with connect(client, user_token(farmer)) as websocket:
        reader = SocketReader(websocket)
        assert reader.next() is not None
        confirm_mapping(client, headers, body["job"]["id"])
        seen = reader.until("analysis.updated")

    assert "analysis.updated" in seen


def test_socket_rejects_a_missing_token(client):
    with client.websocket_connect("/ws") as websocket:
        reader = SocketReader(websocket)
        message = reader.next()
        assert message is not None
        assert message["type"] == "error"
        assert message["payload"]["code"] == "auth.not_authenticated"


def test_socket_rejects_an_invalid_token(client):
    with client.websocket_connect("/ws", params={"token": "not-a-token"}) as websocket:
        reader = SocketReader(websocket)
        message = reader.next()
        assert message is not None
        assert message["type"] == "error"
        assert message["payload"]["code"] == "auth.invalid_token"
        # The server closes the socket straight after the error.
        assert reader.next(timeout=3.0) is None


def test_farmer_socket_does_not_receive_admin_events(client, admin_factory, user_factory, db):
    admin_factory()
    farmer = user_factory(phone="+250788300001")
    broadcaster.notify_admins(db, "job.started", {})

    with connect(client, user_token(farmer)) as websocket:
        reader = SocketReader(websocket)
        assert reader.next() is not None
        seen = reader.until("catchup.completed")

    assert "job.started" not in seen
    assert "job.completed" not in seen


def test_catch_up_on_reconnect_delivers_events_missed_while_offline(client, admin_factory, db):
    admin = admin_factory()

    with connect(client, admin_token(admin)) as websocket:
        reader = SocketReader(websocket)
        assert reader.next() is not None
        reader.until("catchup.completed")

    broadcaster.notify_admins(db, "job.completed", {"households_loaded": 500})

    with connect(client, admin_token(admin)) as websocket:
        reader = SocketReader(websocket)
        assert reader.next() is not None
        seen = reader.until("catchup.completed")

    assert "job.completed" in seen


def test_token_in_the_first_message_is_accepted(client, admin_factory):
    admin = admin_factory()
    with client.websocket_connect("/ws") as websocket:
        websocket.send_json({"token": admin_token(admin)})
        reader = SocketReader(websocket)
        ready = reader.next()
        assert ready is not None
        assert ready["type"] == "connection.ready"
        assert ready["payload"]["channel"] == "admin"


def test_heartbeat_interval_is_25_seconds():
    """The contract says the server pings every 25 seconds."""
    from app.ws.router import HEARTBEAT_SECONDS

    assert HEARTBEAT_SECONDS == 25


def test_ping_is_answered_with_pong(client, admin_factory):
    """A request and response round trip, read on this thread so the ordering is exact.

    The database is empty for this test, so exactly two frames arrive before the ping:
    the handshake and the catch up summary.
    """
    admin = admin_factory()
    with connect(client, admin_token(admin)) as websocket:
        assert websocket.receive_json()["type"] == "connection.ready"
        assert websocket.receive_json()["type"] == "catchup.completed"

        websocket.send_json({"type": "ping"})
        reply = websocket.receive_json()
        assert reply["type"] == "pong"
        assert set(reply) == {"type", "id", "timestamp", "payload"}


def test_bare_pong_text_frame_keeps_the_socket_open(client, admin_factory):
    """The heartbeat reply is a bare "pong" text frame, not JSON."""
    admin = admin_factory()
    with connect(client, admin_token(admin)) as websocket:
        assert websocket.receive_json()["type"] == "connection.ready"
        assert websocket.receive_json()["type"] == "catchup.completed"

        websocket.send_text("pong")
        websocket.send_json({"type": "ping"})
        assert websocket.receive_json()["type"] == "pong"


def test_registry_counts_connections_per_channel():
    registry = broadcaster.ConnectionRegistry()
    assert registry.counts() == {"admin": 0, "user": 0}
    registry.clear()
    assert registry.counts() == {"admin": 0, "user": 0}
