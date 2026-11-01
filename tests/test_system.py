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
    # Read from the published OpenAPI schema — the contract a client integrates against —
    # rather than app.routes, whose shape is a framework internal that changes between
    # FastAPI versions.
    paths = set(client.get("/openapi.json").json()["paths"])
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


@pytest.mark.parametrize("path", ["/api/v1/ls/webhook", "/api/v1/ls/sync", "/api/v1/ls/pull",
                                  "/api/v1/project/admin/task-store"])
def test_the_retired_label_studio_endpoints_are_gone(client, path):
    """Label Studio is retired. Its webhook was the one unauthenticated write path into
    scoring; with the routes removed, nothing answers there at all."""
    assert client.post(path, json={"action": "ANNOTATION_CREATED"},
                       headers={"X-Ls-Secret": "anything"}).status_code == 404


# ── which build is answering (the deployed commit) ─────────────────────────────
#
# `version` is a constant in the source and has never distinguished one deploy from another.
# A post-merge audit could confirm what was on main and nothing at all about what was
# serving requests, which is why four merged commits could not be verified in production.
# `RENDER_GIT_COMMIT` is a documented default on every Render runtime.

from app.api.v1 import system as system_mod


@pytest.mark.parametrize("env, expect", [
    ("4a1298f0123456789abcdef", "4a1298f"),   # a real SHA, truncated to seven
    ("4a1298f", "4a1298f"),                   # exactly seven, unchanged
    ("abc", "abc"),                           # shorter than seven: reported as it is
    ("\n4a1298f\n", "4a1298f"),               # whitespace from the environment
    ("  4a1298f0123  ", "4a1298f"),
])
def test_the_commit_is_reported_truncated(monkeypatch, client, env, expect):
    monkeypatch.setenv("RENDER_GIT_COMMIT", env)
    body = client.get("/api/v1/health").json()
    assert body["commit"] == expect, body
    assert len(body["commit"]) <= system_mod.COMMIT_CHARS


@pytest.mark.parametrize("env", [None, "", "   ", "\n\t "])
def test_an_absent_or_blank_commit_reads_as_unknown(monkeypatch, client, env):
    """The honest answer when the environment said nothing. Anywhere but Render -- a laptop,
    a test run, another host -- this is the expected value."""
    if env is None:
        monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    else:
        monkeypatch.setenv("RENDER_GIT_COMMIT", env)
    assert client.get("/api/v1/health").json()["commit"] == "unknown"


def test_a_value_that_is_not_a_sha_is_shown_rather_than_hidden(monkeypatch, client):
    """Deliberately not validated. If this is ever set to something that is not a commit,
    seeing the wrong value is more useful than seeing "unknown", which would hide the
    misconfiguration behind the same string a correctly unset variable produces.
    """
    monkeypatch.setenv("RENDER_GIT_COMMIT", "not-a-sha-but-set")
    assert client.get("/api/v1/health").json()["commit"] == "not-a-s"


def test_the_existing_fields_are_untouched(monkeypatch, client):
    """Additive only: anything written against the three original fields keeps working.
    scripts/ops_check.py reads this endpoint for its status code alone."""
    monkeypatch.setenv("RENDER_GIT_COMMIT", "4a1298f")
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["version"] and isinstance(body["version"], str)
    assert body["timestamp"]
    assert set(body) == {"status", "version", "timestamp", "commit"}, set(body)


def test_the_commit_never_reveals_anything_but_the_commit(monkeypatch, client):
    """A guard on the obvious mistake: reading the whole environment into a response."""
    monkeypatch.setenv("RENDER_GIT_COMMIT", "4a1298f")
    monkeypatch.setenv("ADMIN_API_KEY", "op_SENTINEL_must_not_appear_here")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "eyJ_SENTINEL_must_not_appear_here")
    text = client.get("/api/v1/health").text
    assert "SENTINEL" not in text, text
