"""Integration: full and incremental reindex from Postgres to Meilisearch."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from meilisearch_python_sdk import AsyncClient
from sqlalchemy import text

from app.sync.runner import (
    LOCK_KEY,
    WATERMARK_KEY,
    acquire_lock,
    full_reindex,
    incremental_reindex,
)
from tests.fixtures.seed import SEED_ROWS, clear_jobs, seed_jobs

pytestmark = pytest.mark.integration


@pytest.fixture
async def reindex_world(pg_engine, meili_env, redis_env):
    """Prepare PG + Meili + Redis and tear them down cleanly between tests."""
    from app.cache.redis_client import get_redis
    from app.db.engine import get_sessionmaker
    from app.search.meili import get_meili_client
    from app.sync.cli import _init_index

    sm = get_sessionmaker()
    async with sm() as s:
        await clear_jobs(s)
        await seed_jobs(s)

    # Ensure index exists + has settings applied.
    await _init_index()

    meili = get_meili_client()
    redis = get_redis()
    try:
        yield {"sessionmaker": sm, "meili": meili, "redis": redis}
    finally:
        async with sm() as s:
            await clear_jobs(s)


async def _doc_count(client: AsyncClient, index_name: str) -> int:
    stats = await client.index(index_name).get_stats()
    return int(stats.number_of_documents)


async def test_full_reindex_pushes_all_rows(reindex_world) -> None:
    sm = reindex_world["sessionmaker"]
    meili: AsyncClient = reindex_world["meili"]
    redis = reindex_world["redis"]

    async with sm() as session:
        result = await full_reindex(session, meili, redis)
    assert result == {"pushed": len(SEED_ROWS), "skipped": 0}

    # Count docs end-to-end.
    assert await _doc_count(meili, "jobs_test") == len(SEED_ROWS)

    # Sample a specific document to confirm flattening worked.
    doc = await meili.index("jobs_test").search("Pharmacy Account Executive")
    hit = next(h for h in doc.hits if h["record_id"] == "seed-eng-1")
    assert hit["location_city"] == "San Marino"
    assert hit["location_state"] == "CA"
    assert hit["salary_min"] == 75000.0
    assert hit["salary_max"] == 100000.0
    assert hit["salary_unit"] == "yearly"
    assert hit["job_category"] == ["retail", "healthcare"]
    assert hit["post_date_ts"] > 0

    # Watermark is advanced.
    wm = await redis.get(WATERMARK_KEY)
    assert wm is not None


async def test_incremental_reindex_only_updated_rows(reindex_world) -> None:
    sm = reindex_world["sessionmaker"]
    meili: AsyncClient = reindex_world["meili"]
    redis = reindex_world["redis"]

    # Seed Meili via full reindex first.
    async with sm() as session:
        await full_reindex(session, meili, redis)

    # Bump one row's updated_at past the watermark.
    target_record_id = "seed-bi-1"
    future = datetime.now(UTC) + timedelta(minutes=5)
    async with sm() as session:
        await session.execute(
            text(
                "UPDATE job_postings SET title = :new_title, updated_at = :ts "
                "WHERE record_id = :rid"
            ),
            {"new_title": "새 제목", "ts": future, "rid": target_record_id},
        )
        await session.commit()

    async with sm() as session:
        result = await incremental_reindex(session, meili, redis)
    assert result == {"pushed": 1, "skipped": 0}

    # The doc in Meili has the updated title.
    async with sm() as session:
        row = (
            await session.execute(
                text("SELECT id FROM job_postings WHERE record_id = :rid"),
                {"rid": target_record_id},
            )
        ).one()
    doc = await meili.index("jobs_test").get_document(row.id)
    assert doc["title"] == "새 제목"
    assert doc["record_id"] == target_record_id


async def test_lock_prevents_overlap(reindex_world) -> None:
    sm = reindex_world["sessionmaker"]
    meili = reindex_world["meili"]
    redis = reindex_world["redis"]

    # Hold the lock; a fresh reindex must report skipped=1 and push nothing.
    acquired = await acquire_lock(redis, 60)
    assert acquired is True

    async with sm() as session:
        result = await full_reindex(session, meili, redis)
    assert result == {"pushed": 0, "skipped": 1}

    # Teardown the lock we manually took.
    await redis.delete(LOCK_KEY)
