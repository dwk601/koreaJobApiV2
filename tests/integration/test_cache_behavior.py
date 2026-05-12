"""Integration: Task 9 — Redis read-through cache + graceful degradation.

Covers:
* Populate-then-hit: the second call is served from Redis even when the
  authoritative Postgres data has been removed.
* Redis-down: endpoints still serve correctly (loader is called directly,
  no 500s) when the injected Redis client cannot connect.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis

from app.dependencies import get_cache
from app.main import create_app
from tests.fixtures.seed import clear_jobs, seed_jobs

pytestmark = pytest.mark.integration


@pytest.fixture
async def client_with_cache(pg_engine, redis_env):
    """FastAPI client with the real testcontainers Redis wired in + seeded PG."""
    from app.db.engine import get_sessionmaker

    sm = get_sessionmaker()
    async with sm() as session:
        await clear_jobs(session)
        await seed_jobs(session)

    app = create_app()
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    finally:
        async with sm() as session:
            await clear_jobs(session)


async def test_stats_populate_then_hit_is_served_from_cache(
    client_with_cache: AsyncClient,
) -> None:
    """First call populates `stats:all`; second call is cache-served even after
    the authoritative DB has been emptied."""
    from app.cache.redis_client import get_redis
    from app.db.engine import get_sessionmaker

    # First call populates the cache.
    r1 = await client_with_cache.get("/api/v1/jobs/stats")
    assert r1.status_code == 200, r1.text
    body1 = r1.json()
    assert body1["total_jobs"] == 5

    # The cache key should now exist under the documented prefix.
    redis = get_redis()
    assert await redis.exists("stats:all") == 1

    # Empty the source of truth. A cache-miss would now return zero rows.
    sm = get_sessionmaker()
    async with sm() as session:
        await clear_jobs(session)

    # Second call must hit the cache (DB is empty) — body is identical.
    r2 = await client_with_cache.get("/api/v1/jobs/stats")
    assert r2.status_code == 200
    assert r2.json() == body1


async def test_detail_by_id_populate_then_hit(client_with_cache: AsyncClient) -> None:
    """`GET /api/v1/jobs/{id}` is cached under `job:id:{n}`; second call is
    served from Redis even after the row has been deleted from PG."""
    from sqlalchemy import text

    from app.cache.redis_client import get_redis
    from app.db.engine import get_sessionmaker

    sm = get_sessionmaker()
    async with sm() as session:
        row = (
            await session.execute(
                text("SELECT id FROM job_postings WHERE record_id='seed-bi-1'")
            )
        ).one()
    job_id = row.id

    r1 = await client_with_cache.get(f"/api/v1/jobs/{job_id}")
    assert r1.status_code == 200
    body1 = r1.json()
    assert body1["record_id"] == "seed-bi-1"

    redis = get_redis()
    assert await redis.exists(f"job:id:{job_id}") == 1

    # Delete the row from PG — a cache miss would now 404.
    async with sm() as session:
        await session.execute(
            text("DELETE FROM job_postings WHERE record_id='seed-bi-1'")
        )
        await session.commit()

    r2 = await client_with_cache.get(f"/api/v1/jobs/{job_id}")
    assert r2.status_code == 200
    assert r2.json() == body1


@pytest.fixture
async def client_with_broken_redis(pg_engine, monkeypatch: pytest.MonkeyPatch):
    """Same as `client_with_cache` but with a Redis client pointed at a closed
    port (short timeouts) injected via dependency override.

    Exercises the graceful-degradation path in `get_or_set`: every GET/SET
    raises, the warning is logged, and the loader returns the authoritative
    response without the request failing.
    """
    from app.db.engine import get_sessionmaker

    sm = get_sessionmaker()
    async with sm() as session:
        await clear_jobs(session)
        await seed_jobs(session)

    broken = Redis(
        host="127.0.0.1",
        port=1,  # reserved TCP port — guaranteed refused
        socket_timeout=0.2,
        socket_connect_timeout=0.2,
        decode_responses=True,
    )

    async def _broken_cache() -> Redis:
        return broken

    app = create_app()
    app.dependency_overrides[get_cache] = _broken_cache

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    finally:
        app.dependency_overrides.clear()
        await broken.aclose()
        async with sm() as session:
            await clear_jobs(session)


async def test_stats_endpoint_still_serves_when_redis_down(
    client_with_broken_redis: AsyncClient,
) -> None:
    """Redis connection errors must not surface as 500s."""
    # Every call in this test path touches a dead Redis on GET+SET. Both
    # must be caught by `get_or_set` and the loader must win.
    for _ in range(2):
        r = await client_with_broken_redis.get("/api/v1/jobs/stats")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total_jobs"] == 5


async def test_detail_endpoint_still_serves_when_redis_down(
    client_with_broken_redis: AsyncClient,
) -> None:
    from sqlalchemy import text

    from app.db.engine import get_sessionmaker

    sm = get_sessionmaker()
    async with sm() as session:
        row = (
            await session.execute(
                text("SELECT id FROM job_postings WHERE record_id='seed-eng-1'")
            )
        ).one()

    r = await client_with_broken_redis.get(f"/api/v1/jobs/{row.id}")
    assert r.status_code == 200, r.text
    assert r.json()["record_id"] == "seed-eng-1"

    # Missing rows still 404 via the loader, not 500 from cache errors.
    r_missing = await client_with_broken_redis.get("/api/v1/jobs/999999")
    assert r_missing.status_code == 404
