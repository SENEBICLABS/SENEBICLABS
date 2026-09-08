"""
System health.

Replaces tests/test_api.py, which exercised the respiratory-intelligence endpoints
(/analyse, /patient, /expert, /waitlist) moved to their own repo in 80a593c. Those 35
tests had been failing on every run since, which is worse than having no tests: a real
failure could not be told apart from the standing noise. Only the health check still
applies to this service.

Run: PYTHONPATH=. pytest tests/test_system.py
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_health_returns_ok(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["version"]


def test_root_identifies_the_service(client):
    body = client.get("/").json()
    assert "Senebiclabs" in body["system"]


def test_the_client_api_surface_is_registered(client):
    """The endpoints a client integrates against. A route silently lost in a refactor
    would otherwise only show up when a client's integration broke."""
    paths = {r.path for r in app.routes}
    for p in ("/api/v1/project/projects", "/api/v1/project/ingest",
              "/api/v1/project/results", "/api/v1/project/templates",
              "/api/v1/project/compare", "/api/v1/project/failures",
              "/api/v1/project/failures/capture", "/api/v1/project/failures/patterns",
              "/api/v1/project/benchmark/promote", "/api/v1/project/benchmark/run",
              "/api/v1/project/webhook/redeliver"):
        assert p in paths, f"client-facing route missing: {p}"


def test_client_endpoints_reject_a_missing_api_key(client):
    """Every client endpoint must be authenticated. An unauthenticated one would expose
    another client's evaluation data."""
    for p in ("/api/v1/project/results?project_id=x",
              "/api/v1/project/compare?baseline=a&candidate=b",
              "/api/v1/project/failures",
              "/api/v1/project/failures/patterns"):
        assert client.get(p).status_code == 401, f"{p} did not require a key"
