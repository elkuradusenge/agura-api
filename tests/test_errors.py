"""Validation and not found responses use the shared error envelope."""

from __future__ import annotations

from tests.conftest import API


def test_unknown_route_returns_the_error_envelope(client):
    response = client.get(f"{API}/does-not-exist")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "resource.not_found"
    assert error["translation_key"] == "errors.resource.not_found"
    assert error["message"]


def test_validation_errors_list_the_offending_fields(client):
    response = client.post(f"{API}/auth/register", json={"phone": "0788123456"})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["translation_key"] == "errors.validation"
    assert any(item["field"] == "password" for item in error["details"])
