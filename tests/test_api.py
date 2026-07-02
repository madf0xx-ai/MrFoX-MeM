"""HTTP API smoke tests. Skipped when the TestClient dep (httpx) is absent."""
from __future__ import annotations

import pytest

pytest.importorskip("httpx", reason="starlette TestClient needs httpx")

from fastapi.testclient import TestClient  # noqa: E402

from core.api import app, valid_project  # noqa: E402


def test_valid_project_slug():
    assert valid_project("my-project")
    assert not valid_project("bad/slug")
    assert not valid_project("")
    # `$` used to match before a terminal newline; fullmatch must reject it.
    assert not valid_project("proj\n")
    assert not valid_project("proj\nx")


def test_health_ok():
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["embed_backend"] in ("fastembed", "hashing")
    assert isinstance(body["degraded"], bool)
    if body["embed_backend"] == "hashing":
        assert body["degraded"] is True
        assert "warning" in body            # loud degraded notice


def test_projects_endpoint_returns_list():
    client = TestClient(app)
    r = client.get("/projects")
    assert r.status_code == 200
    assert isinstance(r.json()["projects"], list)


def test_tree_rejects_bad_project():
    client = TestClient(app)
    r = client.get("/tree", params={"project": "bad/slug"})
    assert r.status_code == 400


def test_delete_project_not_found():
    client = TestClient(app)
    r = client.delete("/project/definitely-not-a-real-project-xyz")
    assert r.status_code == 404


def test_delete_project_rejects_bad_name():
    client = TestClient(app)
    r = client.delete("/project/bad!name")
    assert r.status_code == 400


def test_relevant_rejects_bad_source():
    client = TestClient(app)
    r = client.get(
        "/relevant",
        params={"project": "demo", "prompt": "hi", "source": "not-a-source"},
    )
    assert r.status_code == 400


def test_security_headers_present():
    client = TestClient(app)
    r = client.get("/health")
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert "Content-Security-Policy" in r.headers
