"""Health and service banner checks."""

from __future__ import annotations

from tests.conftest import API


def test_health_returns_service_metadata(client):
    response = client.get(f"{API}/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == "0.1.0"
    assert body["environment"] == "test"


def test_readiness_checks_the_database(client):
    response = client.get(f"{API}/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_root_banner_points_to_docs(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["docs"] == "/docs"


def test_openapi_schema_is_published(client):
    response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert f"{API}/auth/login" in paths
    assert f"{API}/admin/users" in paths
    assert f"{API}/health" in paths
