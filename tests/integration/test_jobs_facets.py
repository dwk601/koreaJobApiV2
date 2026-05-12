"""Integration: ``GET /api/v1/jobs/facets`` — filter-aware counts."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.sync.cli import _init_index
from app.sync.runner import full_reindex
from tests.fixtures.seed import clear_jobs, seed_jobs

pytestmark = pytest.mark.integration


@pytest.fixture
async def facets_world(pg_engine, meili_env, redis_env):
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


async def test_facets_unfiltered_match_all_seed(facets_world: AsyncClient) -> None:
    r = await facets_world.get("/api/v1/jobs/facets")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_estimated"] == 5

    fac = body["facets"]
    assert fac["source"] == {"gtksa": 2, "indeed": 1, "linkedin": 1, "koreadaily": 1}
    assert fac["language"] == {"english": 2, "korean": 2, "bilingual": 1}
    # Salary buckets sum to the total.
    assert sum(fac["salary_bucket"].values()) == body["total_estimated"]


async def test_facets_filtered_by_language(facets_world: AsyncClient) -> None:
    r = await facets_world.get("/api/v1/jobs/facets", params={"language": "korean"})
    body = r.json()
    assert body["total_estimated"] == 2
    fac = body["facets"]
    # Only Korean rows should be present in any facet bucket.
    assert set(fac["source"]) <= {"gtksa", "koreadaily"}
    assert sum(fac["salary_bucket"].values()) == 2


async def test_facets_filtered_by_salary_range(facets_world: AsyncClient) -> None:
    r = await facets_world.get(
        "/api/v1/jobs/facets",
        params={"salary_min": 50000, "salary_unit": "yearly"},
    )
    body = r.json()
    # Only seed-eng-1 (75k min) and seed-bi-1 (55k min) match.
    assert body["total_estimated"] == 2
