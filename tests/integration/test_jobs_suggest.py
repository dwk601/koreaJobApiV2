"""Integration: ``GET /api/v1/jobs/suggest`` — title/company autocomplete."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.sync.cli import _init_index
from app.sync.runner import full_reindex
from tests.fixtures.seed import clear_jobs, seed_jobs

pytestmark = pytest.mark.integration


@pytest.fixture
async def suggest_world(pg_engine, meili_env, redis_env):
    from app.cache.redis_client import get_redis
    from app.db.engine import get_sessionmaker
    from app.search.meili import get_meili_client

    sm = get_sessionmaker()
    async with sm() as s:
        await clear_jobs(s)
        await seed_jobs(s)

    await _init_index()
    async with sm() as s:
        await full_reindex(s, get_meili_client(), get_redis())

    app = create_app()
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    finally:
        async with sm() as s:
            await clear_jobs(s)


async def test_suggest_empty_q_is_rejected(suggest_world: AsyncClient) -> None:
    r = await suggest_world.get("/api/v1/jobs/suggest", params={"q": ""})
    assert r.status_code == 422


async def test_suggest_english_prefix(suggest_world: AsyncClient) -> None:
    r = await suggest_world.get("/api/v1/jobs/suggest", params={"q": "pharm", "limit": 5})
    assert r.status_code == 200, r.text
    body = r.json()
    values = [item["value"] for item in body["items"]]
    assert any("Pharmacy" in v for v in values)
    # Type is one of the allowed enum strings.
    for item in body["items"]:
        assert item["type"] in {"title", "company"}


async def test_suggest_korean_prefix(suggest_world: AsyncClient) -> None:
    r = await suggest_world.get("/api/v1/jobs/suggest", params={"q": "현대"})
    assert r.status_code == 200
    body = r.json()
    assert any("현대" in item["value"] for item in body["items"])


async def test_suggest_limit_caps_results(suggest_world: AsyncClient) -> None:
    # seeded rows share the company "아이씨엔그룹" twice; dedup must collapse.
    r = await suggest_world.get("/api/v1/jobs/suggest", params={"q": "아이씨엔", "limit": 8})
    body = r.json()
    values = [item["value"].casefold() for item in body["items"]]
    assert len(values) == len(set(values))  # deduped


async def test_suggest_limit_out_of_range_rejected(suggest_world: AsyncClient) -> None:
    r = await suggest_world.get("/api/v1/jobs/suggest", params={"q": "x", "limit": 50})
    assert r.status_code == 422
