"""Administrator user management: listing, suspension and password reset."""

from __future__ import annotations

from app.models.enums import UserStatus
from tests.conftest import API, admin_headers


def test_list_users_paginates_filters_and_sorts(client, admin_factory, user_factory):
    admin = admin_factory()
    headers = admin_headers(admin)
    for index in range(5):
        user_factory(phone=f"+25078800000{index}")

    response = client.get(f"{API}/admin/users", params={"page": 2, "page_size": 2}, headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["pagination"] == {"page": 2, "page_size": 2, "total": 5, "total_pages": 3}
    assert len(body["items"]) == 2

    ascending = client.get(
        f"{API}/admin/users", params={"sort": "phone", "order": "asc"}, headers=headers
    )
    phones = [item["phone"] for item in ascending.json()["items"]]
    assert phones == sorted(phones)

    filtered = client.get(f"{API}/admin/users", params={"q": "000003"}, headers=headers)
    assert [item["phone"] for item in filtered.json()["items"]] == ["+250788000003"]


def test_user_detail_returns_404_for_unknown_id(client, admin_factory):
    admin = admin_factory()
    response = client.get(
        f"{API}/admin/users/00000000-0000-0000-0000-000000000000",
        headers=admin_headers(admin),
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "user.not_found"


def test_suspend_and_reactivate_a_farmer(client, admin_factory, user_factory):
    admin = admin_factory()
    user = user_factory()
    headers = admin_headers(admin)

    suspended = client.post(f"{API}/admin/users/{user.id}/suspend", headers=headers)
    assert suspended.status_code == 200
    assert suspended.json()["status"] == UserStatus.SUSPENDED.value
    assert suspended.json()["suspended_at"] is not None

    reactivated = client.post(f"{API}/admin/users/{user.id}/reactivate", headers=headers)
    assert reactivated.status_code == 200
    assert reactivated.json()["status"] == UserStatus.ACTIVE.value
    assert reactivated.json()["suspended_at"] is None


def test_admin_password_reset_issues_a_usable_temporary_password(
    client, admin_factory, user_factory
):
    admin = admin_factory()
    user = user_factory(phone="+250788123999")
    headers = admin_headers(admin)

    response = client.post(f"{API}/admin/users/{user.id}/reset-password", headers=headers)
    assert response.status_code == 200
    temporary_password = response.json()["temporary_password"]
    assert len(temporary_password) >= 8

    login = client.post(
        f"{API}/auth/login", json={"phone": "0788123999", "password": temporary_password}
    )
    assert login.status_code == 200


def test_reset_password_revokes_existing_sessions(client, admin_factory, user_factory):
    admin = admin_factory()
    user = user_factory(phone="+250788124000")
    headers = admin_headers(admin)

    registered = client.post(
        f"{API}/auth/login", json={"phone": "0788124000", "password": "FarmerPass123!"}
    )
    assert registered.status_code == 200
    refresh_token = registered.json()["refresh_token"]

    client.post(f"{API}/admin/users/{user.id}/reset-password", headers=headers)
    replay = client.post(f"{API}/auth/refresh", json={"refresh_token": refresh_token})
    assert replay.status_code == 401
