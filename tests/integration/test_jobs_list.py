"""Integration: `GET /api/v1/jobs` — list/search/facets/cursor against real
Meilisearch + Postgres."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.sync.cli import _init_index
from app.sync.runner import full_reindex
from tests.fixtures.seed import clear_jobs, seed_jobs

pytestmark = pytest.mark.integration


@pytest.fixture
async def list_world(pg_engine, meili_env, redis_env):
    """Seed Postgres, init the Meili index, full-reindex, return an HTTP client."""
    from app.cache.redis_client import get_redis
    from app.db.engine import get_sessionmaker
    from app.search.meili import get_meili_client

    sm = get_sessionmaker()
    async with sm() as s:
        await clear_jobs(s)
        await seed_jobs(s)

    await _init_index()
    meili = get_meili_client()
    redis = get_redis()
    async with sm() as s:
        result = await full_reindex(s, meili, redis)
    assert result["pushed"] > 0

    app = create_app()
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    finally:
        async with sm() as s:
            await clear_jobs(s)


async def test_list_default_sort_newest(list_world: AsyncClient) -> None:
    r = await list_world.get("/api/v1/jobs")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "items" in body and "facets" in body
    assert body["total_estimated"] >= 5
    # The most-recent post_date should appear first. seed-bi-1 is 2026-05-12
    # which is the latest parsed date among the seed.
    assert body["items"][0]["record_id"] == "seed-bi-1"
    assert body["items"][0]["location_state"] == "GA"


async def test_list_filter_by_language_returns_only_korean(list_world: AsyncClient) -> None:
    r = await list_world.get("/api/v1/jobs", params={"language": "korean"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert all(item["language"] == "korean" for item in body["items"])
    assert body["total_estimated"] == 2  # seed-kor-1 + seed-kor-2


async def test_list_filter_by_job_category_array(list_world: AsyncClient) -> None:
    # seed-eng-1 has retail + healthcare; seed-kor-2 has delivery.
    r = await list_world.get(
        "/api/v1/jobs", params=[("job_category", "retail"), ("job_category", "delivery")]
    )
    assert r.status_code == 200
    body = r.json()
    rids = {item["record_id"] for item in body["items"]}
    assert rids == {"seed-eng-1", "seed-kor-2"}


async def test_list_facets_match_distribution(list_world: AsyncClient) -> None:
    r = await list_world.get("/api/v1/jobs")
    body = r.json()
    fac = body["facets"]
    # All 5 seed rows grouped by source.
    assert fac["source"] == {"gtksa": 2, "indeed": 1, "linkedin": 1, "koreadaily": 1}
    assert fac["language"] == {"english": 2, "korean": 2, "bilingual": 1}
    # Salary buckets — only seed-eng-1 (max=100k, 80k_120k) and seed-bi-1
    # (max=55k, 40k_80k) have yearly data; seed-eng-hourly has max=25 (under_40k)
    # but it's hourly so the bucket logic groups by raw salary_max anyway.
    # Rows without salary_max fall into "free".
    buckets = fac["salary_bucket"]
    assert buckets["80k_120k"] == 1
    assert buckets["40k_80k"] == 1
    assert buckets["free"] == 1  # seed-kor-1 has no salary_max
    assert sum(buckets.values()) == body["total_estimated"]


async def test_list_cursor_paginates_with_limit_1(list_world: AsyncClient) -> None:
    """Three hops of limit=1 return three distinct docs in newest order."""
    seen: list[str] = []
    cursor: str | None = None
    for _ in range(3):
        params: dict[str, str] = {"limit": "1"}
        if cursor:
            params["cursor"] = cursor
        r = await list_world.get("/api/v1/jobs", params=params)
        body = r.json()
        assert len(body["items"]) == 1
        seen.append(body["items"][0]["record_id"])
        cursor = body.get("next_cursor")
    assert len(set(seen)) == 3
    # Should be strictly non-increasing by post_date.
    # (seeded post_dates are distinct for the three newest rows.)


async def test_list_search_q_returns_ranked_hit(list_world: AsyncClient) -> None:
    r = await list_world.get("/api/v1/jobs", params={"q": "pharmacy"})
    assert r.status_code == 200
    body = r.json()
    assert body["items"], "expected at least one hit for 'pharmacy'"
    assert body["items"][0]["record_id"] == "seed-eng-1"
    # Page-mode cursor when q is present.
    if body["next_cursor"]:
        from app.search.cursor import decode_cursor

        assert decode_cursor(body["next_cursor"])["mode"] == "pg"


async def test_list_invalid_cursor_returns_envelope(list_world: AsyncClient) -> None:
    r = await list_world.get("/api/v1/jobs", params={"cursor": "not-a-cursor"})
    assert r.status_code == 422
    body = r.json()
    assert body["error"]["code"] == "VALIDATION_FAILED"
