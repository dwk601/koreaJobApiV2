"""Integration tests for `/api/v1/jobs/{id}`, `/record/{rid}`, `/stats`.

Uses the testcontainers Postgres fixture + seeded rows.
Runs the FastAPI app via httpx AsyncClient against an ASGI transport — no
Uvicorn process needed.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.main import create_app
from tests.fixtures.seed import clear_jobs, seed_jobs

pytestmark = pytest.mark.integration


@pytest.fixture
async def client(pg_engine):
    """FastAPI TestClient wired to the test Postgres."""
    # Seed data.
    from app.db.engine import get_sessionmaker

    sm = get_sessionmaker()
    async with sm() as session:
        await clear_jobs(session)
        await seed_jobs(session)

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    async with sm() as session:
        await clear_jobs(session)


async def test_get_by_id_happy_path(client: AsyncClient) -> None:
    # Fetch all seed ids first (autoincrement values differ between runs).
    from app.db.engine import get_sessionmaker

    sm = get_sessionmaker()
    async with sm() as session:
        row = (await session.execute(
            text("SELECT id FROM job_postings WHERE record_id='seed-eng-1'"))
        ).one()

    r = await client.get(f"/api/v1/jobs/{row.id}")
    assert r.status_code == 200
    body = r.json()
    assert body["record_id"] == "seed-eng-1"
    assert body["title"] == "Pharmacy Account Executive"
    assert body["location"] == {"raw": "San Marino, CA 91108",
                                 "city": "San Marino", "state": "CA"}
    assert body["salary"]["unit"] == "yearly"
    assert body["job_category"] == ["retail", "healthcare"]


async def test_get_by_id_not_found(client: AsyncClient) -> None:
    r = await client.get("/api/v1/jobs/999999")
    assert r.status_code == 404
    body = r.json()
    assert body == {
        "error": {"code": "NOT_FOUND",
                  "message": "Job not found",
                  "detail": {"id": 999999}}
    }


async def test_get_by_record_id_happy_path(client: AsyncClient) -> None:
    r = await client.get("/api/v1/jobs/record/seed-bi-1")
    assert r.status_code == 200
    body = r.json()
    assert body["record_id"] == "seed-bi-1"
    assert body["language"] == "bilingual"
    assert body["salary"]["min"] == 55000.0
    assert body["post_date"] == "2026-05-12"


async def test_get_by_record_id_not_found(client: AsyncClient) -> None:
    r = await client.get("/api/v1/jobs/record/nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"
    assert r.json()["error"]["detail"] == {"record_id": "nope"}


async def test_stats_numbers_match_seed(client: AsyncClient) -> None:
    r = await client.get("/api/v1/jobs/stats")
    assert r.status_code == 200
    body = r.json()

    assert body["total_jobs"] == 5

    assert body["by_source"] == {"gtksa": 2, "indeed": 1, "linkedin": 1, "koreadaily": 1}
    assert body["by_language"] == {"english": 2, "korean": 2, "bilingual": 1}

    # job_category counts (sum across rows; each tag counted once per row).
    cats = body["by_category"]
    assert cats["office"] == 2
    assert cats["warehouse"] == 2
    assert cats["manufacturing"] == 2
    assert cats["retail"] == 1
    assert cats["healthcare"] == 1
    assert cats["delivery"] == 1

    # Salary stats: only parsed yearly rows contribute (seed-eng-1, seed-bi-1).
    s = body["salary_stats"]
    assert s["sample_size"] == 2
    assert s["min_salary"] == 55000.0
    assert s["max_salary"] == 100000.0
    # avg((75+100)/2, (55+55)/2) = (87.5 + 55) / 2 = 71.25 k
    assert abs(s["avg_salary"] - 71250.0) < 1e-6
