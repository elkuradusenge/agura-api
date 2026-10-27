"""Profile update and deletion for farmers."""

from __future__ import annotations

from tests.conftest import API, user_headers


def test_patch_me_updates_language_and_location(client, user_factory):
    user = user_factory()
    response = client.patch(
        f"{API}/me",
        json={
            "language": "en",
            "province": "Kigali City",
            "district": "Gasabo",
            "reminder_morning_time": "06:30:00",
        },
        headers=user_headers(user),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["language"] == "en"
    assert body["province"] == "Kigali City"
    assert body["reminder_morning_time"] == "06:30:00"


def test_patch_me_rejects_unknown_language(client, user_factory):
    user = user_factory()
    response = client.patch(f"{API}/me", json={"language": "sw"}, headers=user_headers(user))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_change_password_requires_the_current_password(client, user_factory):
    user = user_factory()
    response = client.post(
        f"{API}/me/password",
        json={"current_password": "wrong-one", "new_password": "AnotherPass123!"},
        headers=user_headers(user),
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "auth.invalid_credentials"

    ok = client.post(
        f"{API}/me/password",
        json={"current_password": "FarmerPass123!", "new_password": "AnotherPass123!"},
        headers=user_headers(user),
    )
    assert ok.status_code == 200


def test_delete_me_anonymises_and_blocks_further_use(client, user_factory):
    user = user_factory(phone="+250788555111")
    headers = user_headers(user)

    deleted = client.delete(f"{API}/me", headers=headers)
    assert deleted.status_code == 200
    assert deleted.json()["deleted_at"]

    blocked = client.get(f"{API}/me", headers=headers)
    assert blocked.status_code == 401

    login = client.post(
        f"{API}/auth/login", json={"phone": "0788555111", "password": "FarmerPass123!"}
    )
    assert login.status_code == 401
