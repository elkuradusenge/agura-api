"""Role based access control for the administrator console."""

from __future__ import annotations

from app.models.enums import AdminRole, AdminStatus, UserStatus
from tests.conftest import API, admin_headers, user_headers


def test_super_admin_sees_every_permission(client, admin_factory):
    admin = admin_factory(role=AdminRole.SUPER_ADMIN)
    response = client.post(
        f"{API}/auth/admin/login",
        json={"identifier": admin.email, "password": "AdminPass123!"},
    )
    assert response.status_code == 200
    account = response.json()["account"]
    assert account["role"] == "super_admin"
    assert "users.write" in account["permissions"]
    assert "audit.read" in account["permissions"]


def test_admin_can_sign_in_with_phone(client, admin_factory):
    admin_factory(role=AdminRole.SUPER_ADMIN, phone="+250788777888")
    response = client.post(
        f"{API}/auth/admin/login",
        json={"identifier": "0788777888", "password": "AdminPass123!"},
    )
    assert response.status_code == 200


def test_admin_login_rejects_bad_password(client, admin_factory):
    admin = admin_factory()
    response = client.post(
        f"{API}/auth/admin/login", json={"identifier": admin.email, "password": "nope-nope"}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "auth.invalid_credentials"


def test_suspended_admin_cannot_sign_in(client, admin_factory):
    admin = admin_factory(status=AdminStatus.SUSPENDED)
    response = client.post(
        f"{API}/auth/admin/login",
        json={"identifier": admin.email, "password": "AdminPass123!"},
    )
    assert response.status_code == 403


def test_support_agent_can_read_users_but_not_the_audit_log(client, admin_factory):
    admin = admin_factory(role=AdminRole.SUPPORT_AGENT)
    headers = admin_headers(admin)
    assert client.get(f"{API}/admin/users", headers=headers).status_code == 200
    audit = client.get(f"{API}/admin/audit-log", headers=headers)
    assert audit.status_code == 403
    assert audit.json()["error"]["code"] == "auth.forbidden"


def test_support_agent_cannot_suspend_a_farmer(client, admin_factory, user_factory):
    admin = admin_factory(role=AdminRole.SUPPORT_AGENT)
    user = user_factory()
    response = client.post(f"{API}/admin/users/{user.id}/suspend", headers=admin_headers(admin))
    assert response.status_code == 403


def test_content_editor_cannot_list_farmers(client, admin_factory):
    admin = admin_factory(role=AdminRole.CONTENT_EDITOR)
    assert client.get(f"{API}/admin/users", headers=admin_headers(admin)).status_code == 403


def test_data_analyst_cannot_suspend_farmers(client, admin_factory, user_factory):
    admin = admin_factory(role=AdminRole.DATA_ANALYST)
    user = user_factory()
    response = client.post(f"{API}/admin/users/{user.id}/suspend", headers=admin_headers(admin))
    assert response.status_code == 403
    assert client.get(f"{API}/admin/users", headers=admin_headers(admin)).status_code == 403


def test_farmer_token_is_rejected_on_admin_routes(client, user_factory):
    user = user_factory()
    response = client.get(f"{API}/admin/users", headers=user_headers(user))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "auth.invalid_token"


def test_admin_token_is_rejected_on_farmer_routes(client, admin_factory):
    admin = admin_factory()
    response = client.get(f"{API}/me", headers=admin_headers(admin))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "auth.invalid_token"


def test_suspended_farmer_keeps_no_access(client, user_factory, db):
    user = user_factory()
    user.status = UserStatus.SUSPENDED
    db.commit()

    response = client.get(f"{API}/me", headers=user_headers(user))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "auth.forbidden"
