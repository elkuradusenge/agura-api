"""Farmer authentication: registration, sign in, tokens and password reset."""

from __future__ import annotations

from tests.conftest import API

PASSWORD = "FarmerPass123!"


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def register(client, phone: str = "0788123456", password: str = PASSWORD):
    return client.post(f"{API}/auth/register", json={"phone": phone, "password": password})


def test_register_normalises_phone_and_returns_tokens(client):
    response = register(client, phone="0788 123 456")
    assert response.status_code == 201
    body = response.json()
    assert body["account"]["phone"] == "+250788123456"
    assert body["account"]["language"] == "rw"
    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0

    me = client.get(f"{API}/me", headers=auth_header(body["access_token"]))
    assert me.status_code == 200
    assert me.json()["phone"] == "+250788123456"


def test_register_rejects_invalid_phone(client):
    response = register(client, phone="12345")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "auth.invalid_phone"
    assert response.json()["error"]["translation_key"] == "errors.auth.invalid_phone"


def test_register_rejects_weak_password(client):
    response = register(client, password="short")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "auth.weak_password"


def test_register_rejects_duplicate_phone(client):
    assert register(client).status_code == 201
    duplicate = register(client, phone="+250788123456")
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "auth.phone_taken"


def test_login_accepts_alternate_phone_formats(client):
    assert register(client).status_code == 201
    for phone in ("0788123456", "+250788123456", "250788123456"):
        response = client.post(f"{API}/auth/login", json={"phone": phone, "password": PASSWORD})
        assert response.status_code == 200, phone


def test_login_with_wrong_password_is_rejected(client):
    assert register(client).status_code == 201
    response = client.post(
        f"{API}/auth/login", json={"phone": "0788123456", "password": "WrongPass123!"}
    )
    assert response.status_code == 401
    error = response.json()["error"]
    assert error["code"] == "auth.invalid_credentials"
    assert error["translation_key"] == "errors.auth.invalid_credentials"


def test_login_unknown_account_is_rejected(client):
    response = client.post(f"{API}/auth/login", json={"phone": "0788999999", "password": PASSWORD})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "auth.invalid_credentials"


def test_refresh_rotates_and_revokes_the_old_token(client):
    body = register(client).json()
    first = client.post(f"{API}/auth/refresh", json={"refresh_token": body["refresh_token"]})
    assert first.status_code == 200
    new_refresh = first.json()["refresh_token"]
    assert new_refresh != body["refresh_token"]

    replay = client.post(f"{API}/auth/refresh", json={"refresh_token": body["refresh_token"]})
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "auth.invalid_token"

    fresh = client.post(f"{API}/auth/refresh", json={"refresh_token": new_refresh})
    assert fresh.status_code == 200


def test_logout_revokes_the_refresh_token(client):
    body = register(client).json()
    logout = client.post(f"{API}/auth/logout", json={"refresh_token": body["refresh_token"]})
    assert logout.status_code == 200
    response = client.post(f"{API}/auth/refresh", json={"refresh_token": body["refresh_token"]})
    assert response.status_code == 401


def test_password_reset_flow(client):
    assert register(client).status_code == 201

    requested = client.post(f"{API}/auth/password-reset/request", json={"phone": "0788123456"})
    assert requested.status_code == 202
    code = requested.json()["debug_code"]
    assert code is not None and len(code) == 6

    confirmed = client.post(
        f"{API}/auth/password-reset/confirm",
        json={"phone": "0788123456", "code": code, "new_password": "BrandNewPass456!"},
    )
    assert confirmed.status_code == 200

    old_password = client.post(
        f"{API}/auth/login", json={"phone": "0788123456", "password": PASSWORD}
    )
    assert old_password.status_code == 401

    new_password = client.post(
        f"{API}/auth/login", json={"phone": "0788123456", "password": "BrandNewPass456!"}
    )
    assert new_password.status_code == 200

    reused = client.post(
        f"{API}/auth/password-reset/confirm",
        json={"phone": "0788123456", "code": code, "new_password": "AnotherPass789!"},
    )
    assert reused.status_code == 422
    assert reused.json()["error"]["code"] == "auth.reset_code_invalid"


def test_password_reset_hides_whether_the_number_exists(client):
    response = client.post(f"{API}/auth/password-reset/request", json={"phone": "0788555555"})
    assert response.status_code == 202
    assert response.json()["debug_code"] is None


def test_reset_request_is_rate_limited(client):
    assert register(client).status_code == 201
    statuses = [
        client.post(f"{API}/auth/password-reset/request", json={"phone": "0788123456"}).status_code
        for _ in range(11)
    ]
    assert statuses[-1] == 429
    assert 429 in statuses


def test_me_requires_a_token(client):
    response = client.get(f"{API}/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "auth.not_authenticated"
