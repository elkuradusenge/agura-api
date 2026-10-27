"""Every administrator write action must leave an audit entry."""

from __future__ import annotations

from tests.conftest import API, admin_headers


def test_suspend_writes_an_audit_entry(client, admin_factory, user_factory):
    admin = admin_factory()
    user = user_factory()
    headers = admin_headers(admin)

    client.post(f"{API}/admin/users/{user.id}/suspend", headers=headers)

    response = client.get(f"{API}/admin/audit-log", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["pagination"]["total"] == 1
    entry = body["items"][0]
    assert entry["action"] == "user.suspend"
    assert entry["entity_type"] == "user"
    assert entry["entity_id"] == str(user.id)
    assert entry["actor_id"] == str(admin.id)
    assert entry["payload"]["phone"] == user.phone


def test_audit_log_can_be_filtered_by_action(client, admin_factory, user_factory):
    admin = admin_factory()
    user = user_factory()
    headers = admin_headers(admin)

    client.post(f"{API}/admin/users/{user.id}/suspend", headers=headers)
    client.post(f"{API}/admin/users/{user.id}/reactivate", headers=headers)

    filtered = client.get(
        f"{API}/admin/audit-log", params={"action": "user.reactivate"}, headers=headers
    )
    assert filtered.status_code == 200
    items = filtered.json()["items"]
    assert len(items) == 1
    assert items[0]["action"] == "user.reactivate"

    all_entries = client.get(f"{API}/admin/audit-log", headers=headers).json()
    assert all_entries["pagination"]["total"] == 2


def test_read_only_admin_actions_do_not_write_audit_entries(client, admin_factory, user_factory):
    admin = admin_factory()
    user = user_factory()
    headers = admin_headers(admin)

    client.get(f"{API}/admin/users", headers=headers)
    client.get(f"{API}/admin/users/{user.id}", headers=headers)

    entries = client.get(f"{API}/admin/audit-log", headers=headers).json()
    assert entries["pagination"]["total"] == 0
