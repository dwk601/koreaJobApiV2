"""Integration: Task 11 — /ready probe, envelope handlers, X-Request-ID.

Covers:
* ``/ready`` 200 when PG + Redis + Meili are all reachable.
* ``/ready`` 503 with per-component ``ok``/``unhealthy`` map when Redis,
  Postgres, or Meilisearch fails (each tested independently).
* Envelope shape for unknown routes (404) and request-validation errors (422).
* ``X-Request-ID`` echoed when supplied and freshly generated when absent,
  on both fast-paths (/health) and real endpoints (/ready).
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis

from app.main import create_app
from app.middleware.request_id import HEADER_NAME

pytestmark = pytest.mark.integration


# ─────────────────────────── /ready: happy path ───────────────────────────


@pytest.fixture
async def ready_client(pg_engine, redis_env, meili_env) -> AsyncClient:
    """Full stack healthy — Postgres, Redis, and Meilisearch containers up."""
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


async def test_ready_200_when_all_upstreams_healthy(
    ready_client: AsyncClient,
) -> None:
    r = await ready_client.get("/ready")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {
        "status": "ok",
        "components": {
            "postgres": "ok",
            "redis": "ok",
            "meilisearch": "ok",
        },
    }


# ─────────────────────────── /ready: per-component failures ───────────────────────────


def _broken_redis() -> Redis:
    """Redis pointed at a closed port — every command raises immediately."""
    return Redis(
        host="127.0.0.1",
        port=1,
        socket_timeout=0.2,
        socket_connect_timeout=0.2,
        decode_responses=True,
    )


class _BrokenMeili:
    """Stub Meili client whose health() always raises."""

    async def health(self) -> Any:
        raise ConnectionError("meili unreachable")

    async def aclose(self) -> None:
        return None


class _BrokenSessionMaker:
    """Callable that, when invoked, yields an async context manager whose
    ``session.execute`` raises. Mimics the shape of ``async_sessionmaker``."""

    def __call__(self) -> Any:
        return self

    async def __aenter__(self) -> Any:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def execute(self, *_: Any, **__: Any) -> None:
        raise ConnectionError("pg unreachable")


async def test_ready_503_when_redis_down(
    pg_engine, meili_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dead Redis must mark only `redis` as unhealthy and flip status to 503."""
    broken = _broken_redis()
    try:
        monkeypatch.setattr("app.routers.health.get_redis", lambda: broken)
        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.get("/ready")
        assert r.status_code == 503
        body = r.json()
        assert body["status"] == "degraded"
        assert body["components"]["redis"] == "unhealthy"
        assert body["components"]["postgres"] == "ok"
        assert body["components"]["meilisearch"] == "ok"
    finally:
        await broken.aclose()


async def test_ready_503_when_meili_down(
    pg_engine, redis_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.routers.health.get_meili_client", lambda: _BrokenMeili()
    )
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/ready")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "degraded"
    assert body["components"] == {
        "postgres": "ok",
        "redis": "ok",
        "meilisearch": "unhealthy",
    }


async def test_ready_503_when_postgres_down(
    redis_env, meili_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.routers.health.get_sessionmaker", lambda: _BrokenSessionMaker()
    )
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/ready")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "degraded"
    assert body["components"]["postgres"] == "unhealthy"
    assert body["components"]["redis"] == "ok"
    assert body["components"]["meilisearch"] == "ok"


async def test_ready_503_reports_every_failing_component(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broken = _broken_redis()
    try:
        monkeypatch.setattr("app.routers.health.get_redis", lambda: broken)
        monkeypatch.setattr(
            "app.routers.health.get_meili_client", lambda: _BrokenMeili()
        )
        monkeypatch.setattr(
            "app.routers.health.get_sessionmaker", lambda: _BrokenSessionMaker()
        )
        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.get("/ready")
        assert r.status_code == 503
        body = r.json()
        assert body["status"] == "degraded"
        assert all(v == "unhealthy" for v in body["components"].values())
    finally:
        await broken.aclose()


# ─────────────────────────── Envelope shape ───────────────────────────


@pytest.fixture
async def app_client() -> AsyncClient:
    """App without upstreams — suitable for tests that only hit middleware/
    exception paths (404/422 on validation, request-id round-trip)."""
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


async def test_unknown_route_returns_envelope_404(app_client: AsyncClient) -> None:
    r = await app_client.get("/not-a-real-route")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


async def test_query_validation_error_returns_envelope_422(
    app_client: AsyncClient,
) -> None:
    # JobListQuery.limit has le=100; 200 triggers FastAPI's RequestValidationError
    # which our override renders as the standard envelope.
    r = await app_client.get("/api/v1/jobs", params={"limit": 200})
    assert r.status_code == 422
    body = r.json()
    assert body["error"]["code"] == "VALIDATION_FAILED"
    errors = body["error"]["detail"]["errors"]
    assert any("limit" in err["loc"] for err in errors)


# ─────────────────────────── X-Request-ID round-trip ───────────────────────────


async def test_request_id_generated_when_header_absent(
    app_client: AsyncClient,
) -> None:
    r = await app_client.get("/health")
    assert r.status_code == 200
    echoed = r.headers[HEADER_NAME]
    parsed = uuid.UUID(echoed)
    assert parsed.version == 4


async def test_request_id_passed_through_when_valid(
    app_client: AsyncClient,
) -> None:
    r = await app_client.get(
        "/health", headers={HEADER_NAME: "integration-trace-42"}
    )
    assert r.status_code == 200
    assert r.headers[HEADER_NAME] == "integration-trace-42"


async def test_request_id_present_on_validation_error_response(
    app_client: AsyncClient,
) -> None:
    """422 responses must still carry the X-Request-ID (outermost middleware
    wraps exception handler output)."""
    r = await app_client.get("/api/v1/jobs", params={"limit": 200})
    assert r.status_code == 422
    uuid.UUID(r.headers[HEADER_NAME])


async def test_request_id_present_on_ready_response(
    ready_client: AsyncClient,
) -> None:
    r = await ready_client.get("/ready")
    assert r.status_code == 200
    uuid.UUID(r.headers[HEADER_NAME])
