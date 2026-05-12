"""Integration: Task 10 — Redis sliding-window rate limit middleware.

Runs a minimal FastAPI app that registers :class:`RateLimitMiddleware`
with a real :mod:`testcontainers` Redis, then fires deterministic bursts
at stub routes — one per bucket class.

Covered scenarios:
* Under-cap requests carry ``X-RateLimit-*`` headers and a decreasing remaining count.
* Bursts past the cap switch to ``429`` with ``Retry-After``.
* ``/api/v1/jobs/suggest`` has an independent (tighter) bucket.
* Skipped paths (``/health``) never consume a slot.
* Distinct clients (distinct ``X-Forwarded-For``) have independent buckets.
* ``RATE_LIMIT_ENABLED=false`` bypasses the middleware entirely.
* Redis unreachable → fail open (requests still 200, no rate-limit headers).
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis

from app.config import Settings
from app.middleware.rate_limit import RateLimitMiddleware

pytestmark = pytest.mark.integration

# Tighten the list cap so we can burst past it in milliseconds.
_LIST_CAP = 5
_SUGGEST_CAP = 3
_DEFAULT_CAP = 4


def _build_test_app(settings: Settings, redis_factory=None) -> FastAPI:
    """Minimal app that mirrors the production paths covered by each bucket."""
    app = FastAPI()
    app.add_middleware(
        RateLimitMiddleware, settings=settings, redis_factory=redis_factory
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/jobs")
    async def list_jobs() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/v1/jobs/facets")
    async def facets() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/v1/jobs/suggest")
    async def suggest() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/v1/jobs/stats")
    async def stats() -> dict[str, bool]:
        return {"ok": True}

    return app


async def _client_for(app: FastAPI) -> AsyncClient:
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# ────────────────────────── Fixtures ──────────────────────────


@pytest.fixture
def limits_settings() -> Settings:
    """Settings with tight caps, enabled, defaults for everything else."""
    return Settings(
        rate_limit_enabled=True,
        rate_limit_list_per_min=_LIST_CAP,
        rate_limit_suggest_per_min=_SUGGEST_CAP,
        rate_limit_default_per_min=_DEFAULT_CAP,
    )


@pytest.fixture
async def rate_limit_app(redis_env, limits_settings: Settings) -> FastAPI:
    """Test app wired to the shared testcontainers Redis."""
    return _build_test_app(limits_settings)


# ────────────────────────── Tests ──────────────────────────


async def test_first_request_has_rate_limit_headers(rate_limit_app: FastAPI) -> None:
    async with await _client_for(rate_limit_app) as client:
        r = await client.get(
            "/api/v1/jobs", headers={"X-Forwarded-For": "203.0.113.1"}
        )
    assert r.status_code == 200
    assert r.headers["X-RateLimit-Limit"] == str(_LIST_CAP)
    assert r.headers["X-RateLimit-Remaining"] == str(_LIST_CAP - 1)
    assert int(r.headers["X-RateLimit-Reset"]) > 0


async def test_remaining_counts_down_with_each_request(rate_limit_app: FastAPI) -> None:
    async with await _client_for(rate_limit_app) as client:
        remaining_seen: list[int] = []
        for _ in range(_LIST_CAP):
            r = await client.get(
                "/api/v1/jobs", headers={"X-Forwarded-For": "203.0.113.2"}
            )
            assert r.status_code == 200
            remaining_seen.append(int(r.headers["X-RateLimit-Remaining"]))
    # Strictly decreasing, hitting zero on the final allowed request.
    assert remaining_seen == list(range(_LIST_CAP - 1, -1, -1))


async def test_burst_past_cap_returns_429_with_retry_after(
    rate_limit_app: FastAPI,
) -> None:
    total = _LIST_CAP + 3
    statuses: list[int] = []
    headers_429: list[dict[str, str]] = []

    async with await _client_for(rate_limit_app) as client:
        for _ in range(total):
            r = await client.get(
                "/api/v1/jobs",
                headers={"X-Forwarded-For": "203.0.113.3"},
            )
            statuses.append(r.status_code)
            if r.status_code == 429:
                headers_429.append(dict(r.headers))

    # First _LIST_CAP are 200, remaining are 429.
    assert statuses[:_LIST_CAP] == [200] * _LIST_CAP
    assert statuses[_LIST_CAP:] == [429] * (total - _LIST_CAP)

    # 429 payload shape + Retry-After header.
    sample = headers_429[0]
    assert "retry-after" in sample
    assert int(sample["retry-after"]) >= 1
    assert sample["x-ratelimit-limit"] == str(_LIST_CAP)
    assert sample["x-ratelimit-remaining"] == "0"


async def test_429_body_is_error_envelope(rate_limit_app: FastAPI) -> None:
    async with await _client_for(rate_limit_app) as client:
        for _ in range(_LIST_CAP):
            await client.get(
                "/api/v1/jobs", headers={"X-Forwarded-For": "203.0.113.4"}
            )
        r = await client.get(
            "/api/v1/jobs", headers={"X-Forwarded-For": "203.0.113.4"}
        )

    assert r.status_code == 429
    body = r.json()
    assert body["error"]["code"] == "RATE_LIMITED"
    assert body["error"]["detail"]["bucket"] == "list"
    assert body["error"]["detail"]["limit"] == _LIST_CAP


async def test_suggest_bucket_has_independent_tighter_cap(
    rate_limit_app: FastAPI,
) -> None:
    async with await _client_for(rate_limit_app) as client:
        ok = 0
        limited = 0
        for _ in range(_SUGGEST_CAP + 2):
            r = await client.get(
                "/api/v1/jobs/suggest",
                headers={"X-Forwarded-For": "203.0.113.5"},
            )
            if r.status_code == 200:
                ok += 1
            elif r.status_code == 429:
                limited += 1

    assert ok == _SUGGEST_CAP
    assert limited == 2

    # A request on the list bucket from the *same* client still has budget
    # (independent bucket).
    async with await _client_for(rate_limit_app) as client:
        r = await client.get(
            "/api/v1/jobs", headers={"X-Forwarded-For": "203.0.113.5"}
        )
    assert r.status_code == 200
    assert r.headers["X-RateLimit-Limit"] == str(_LIST_CAP)


async def test_health_endpoint_is_never_rate_limited(rate_limit_app: FastAPI) -> None:
    async with await _client_for(rate_limit_app) as client:
        for _ in range(_LIST_CAP * 5):
            r = await client.get(
                "/health", headers={"X-Forwarded-For": "203.0.113.6"}
            )
            assert r.status_code == 200
            # Skipped paths must NOT advertise a rate-limit budget.
            assert "X-RateLimit-Limit" not in r.headers


async def test_distinct_clients_have_independent_buckets(
    rate_limit_app: FastAPI,
) -> None:
    async with await _client_for(rate_limit_app) as client:
        # Client A exhausts the bucket.
        for _ in range(_LIST_CAP):
            r = await client.get(
                "/api/v1/jobs", headers={"X-Forwarded-For": "203.0.113.7"}
            )
            assert r.status_code == 200
        r_blocked = await client.get(
            "/api/v1/jobs", headers={"X-Forwarded-For": "203.0.113.7"}
        )
        assert r_blocked.status_code == 429

        # Client B sharing the same process but different XFF — fresh budget.
        r_other = await client.get(
            "/api/v1/jobs", headers={"X-Forwarded-For": "203.0.113.8"}
        )
        assert r_other.status_code == 200
        assert r_other.headers["X-RateLimit-Remaining"] == str(_LIST_CAP - 1)


async def test_rate_limit_disabled_flag_bypasses_middleware(redis_env) -> None:
    settings = Settings(
        rate_limit_enabled=False,
        rate_limit_list_per_min=2,  # would otherwise block after 2 hits
    )
    app = _build_test_app(settings)

    async with await _client_for(app) as client:
        for _ in range(10):
            r = await client.get(
                "/api/v1/jobs", headers={"X-Forwarded-For": "203.0.113.9"}
            )
            assert r.status_code == 200
            # No rate-limit headers when the middleware is disabled.
            assert "X-RateLimit-Limit" not in r.headers


async def test_redis_down_fails_open_no_500s(limits_settings: Settings) -> None:
    """A Redis pointed at a closed port triggers RedisError on every command.
    The middleware must log WARN and let the request through (no 500, no
    rate-limit headers)."""
    broken = Redis(
        host="127.0.0.1",
        port=1,  # reserved; refused immediately on Linux
        socket_timeout=0.2,
        socket_connect_timeout=0.2,
        decode_responses=True,
    )

    def _broken_factory() -> Redis:
        return broken

    app = _build_test_app(limits_settings, redis_factory=_broken_factory)
    try:
        async with await _client_for(app) as client:
            for _ in range(10):  # well past the _LIST_CAP of 5
                r = await client.get(
                    "/api/v1/jobs", headers={"X-Forwarded-For": "203.0.113.10"}
                )
                assert r.status_code == 200
                # Fail-open path skips header injection.
                assert "X-RateLimit-Limit" not in r.headers
    finally:
        await broken.aclose()
