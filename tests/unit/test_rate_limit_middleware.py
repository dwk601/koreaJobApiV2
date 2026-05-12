"""Unit tests for the pure helpers in ``app.middleware.rate_limit``.

The stateful dispatch logic (sorted-set + 429) is exercised in the
integration suite against a real Redis container.
"""
from __future__ import annotations

import pytest
from starlette.requests import Request

from app.config import Settings
from app.middleware.rate_limit import (
    SKIP_PATHS,
    classify_bucket,
    resolve_client_key,
    should_skip,
)

# ─────────────────────── should_skip ───────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "/health",
        "/ready",
        "/docs",
        "/openapi.json",
        "/redoc",
        "/docs/oauth2-redirect",
    ],
)
def test_should_skip_returns_true_for_documented_paths(path: str) -> None:
    assert should_skip(path) is True


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/jobs",
        "/api/v1/jobs/",
        "/api/v1/jobs/suggest",
        "/api/v1/jobs/facets",
        "/api/v1/jobs/123",
        "/api/v1/jobs/stats",
        "/api/v1/jobs/record/abc",
        "/",
        "/something-else",
    ],
)
def test_should_skip_returns_false_for_rate_limited_paths(path: str) -> None:
    assert should_skip(path) is False


def test_skip_paths_is_the_documented_set() -> None:
    assert frozenset(
        {"/health", "/ready", "/docs", "/openapi.json", "/redoc"}
    ) == SKIP_PATHS


# ─────────────────────── classify_bucket ───────────────────────


@pytest.fixture
def default_settings() -> Settings:
    return Settings(
        rate_limit_list_per_min=120,
        rate_limit_suggest_per_min=30,
        rate_limit_default_per_min=60,
    )


@pytest.mark.parametrize(
    "path",
    ["/api/v1/jobs", "/api/v1/jobs/", "/api/v1/jobs/facets", "/api/v1/jobs/facets/"],
)
def test_classify_bucket_list(path: str, default_settings: Settings) -> None:
    bucket, limit = classify_bucket(path, default_settings)
    assert bucket == "list"
    assert limit == 120


@pytest.mark.parametrize("path", ["/api/v1/jobs/suggest", "/api/v1/jobs/suggest/"])
def test_classify_bucket_suggest(path: str, default_settings: Settings) -> None:
    bucket, limit = classify_bucket(path, default_settings)
    assert bucket == "suggest"
    assert limit == 30


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/jobs/stats",
        "/api/v1/jobs/123",
        "/api/v1/jobs/record/abc",
        "/",
        "/something-else",
    ],
)
def test_classify_bucket_default(path: str, default_settings: Settings) -> None:
    bucket, limit = classify_bucket(path, default_settings)
    assert bucket == "default"
    assert limit == 60


def test_classify_bucket_reads_limits_from_settings() -> None:
    s = Settings(
        rate_limit_list_per_min=7,
        rate_limit_suggest_per_min=8,
        rate_limit_default_per_min=9,
    )
    assert classify_bucket("/api/v1/jobs", s) == ("list", 7)
    assert classify_bucket("/api/v1/jobs/suggest", s) == ("suggest", 8)
    assert classify_bucket("/api/v1/jobs/stats", s) == ("default", 9)


# ─────────────────────── resolve_client_key ───────────────────────


def _make_request(
    headers: dict[str, str] | None = None,
    client: tuple[str, int] | None = ("127.0.0.1", 12345),
) -> Request:
    """Minimal Starlette ``Request`` with the given ASGI scope details."""
    raw_headers = [
        (k.lower().encode("latin-1"), v.encode("latin-1"))
        for k, v in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": raw_headers,
        "client": client,
        "server": ("testserver", 80),
        "scheme": "http",
    }
    return Request(scope)


def test_resolve_client_key_prefers_x_forwarded_for() -> None:
    req = _make_request(
        headers={"X-Forwarded-For": "203.0.113.7, 10.0.0.1, 172.16.0.1"},
        client=("127.0.0.1", 1000),
    )
    assert resolve_client_key(req) == "203.0.113.7"


def test_resolve_client_key_trims_xff_whitespace() -> None:
    req = _make_request(headers={"X-Forwarded-For": "   198.51.100.4  "})
    assert resolve_client_key(req) == "198.51.100.4"


def test_resolve_client_key_ignores_empty_xff_first_entry() -> None:
    # Malformed XFF with empty first segment → fall back to client.host.
    req = _make_request(
        headers={"X-Forwarded-For": ",10.0.0.1"},
        client=("198.51.100.9", 443),
    )
    assert resolve_client_key(req) == "198.51.100.9"


def test_resolve_client_key_falls_back_to_request_client_host() -> None:
    req = _make_request(headers=None, client=("192.0.2.33", 1234))
    assert resolve_client_key(req) == "192.0.2.33"


def test_resolve_client_key_returns_unknown_when_no_info() -> None:
    req = _make_request(headers=None, client=None)
    assert resolve_client_key(req) == "unknown"
