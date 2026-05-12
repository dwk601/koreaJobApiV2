"""Smoke tests for the FastAPI app factory: /health and CORS wiring."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def _client(**overrides: object) -> TestClient:
    settings = Settings(**overrides)  # type: ignore[arg-type]
    return TestClient(create_app(settings))


def test_health_ok() -> None:
    with _client(cors_origins=[]) as c:
        r = c.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_cors_allows_listed_origin() -> None:
    with _client(cors_origins=["https://example.test"]) as c:
        r = c.get("/health", headers={"Origin": "https://example.test"})
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "https://example.test"


def test_cors_preflight_for_listed_origin() -> None:
    with _client(cors_origins=["https://example.test"]) as c:
        r = c.options(
            "/health",
            headers={
                "Origin": "https://example.test",
                "Access-Control-Request-Method": "GET",
            },
        )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "https://example.test"
    assert "GET" in r.headers.get("access-control-allow-methods", "")


def test_cors_rejects_unlisted_origin() -> None:
    with _client(cors_origins=["https://allowed.test"]) as c:
        r = c.get("/health", headers={"Origin": "https://evil.test"})
    # Starlette's CORS middleware omits the allow-origin header when the
    # requesting origin isn't allowed; the response body still comes through
    # (FastAPI is happy), but the browser would block it.
    assert r.status_code == 200
    assert "access-control-allow-origin" not in r.headers


def test_cors_wildcard() -> None:
    with _client(cors_origins=["*"]) as c:
        r = c.get("/health", headers={"Origin": "https://anything.test"})
    assert r.headers.get("access-control-allow-origin") == "*"
