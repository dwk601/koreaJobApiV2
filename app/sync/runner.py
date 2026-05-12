"""Sync orchestration (full + incremental reindex).

Both paths read from Postgres via SQLAlchemy async and push batched
documents to Meilisearch. An advisory lock in Redis prevents overlapping
runs; the incremental watermark is stored at ``sync:jobs:watermark``.
"""
from __future__ import annotations

from datetime import UTC, datetime

from meilisearch_python_sdk import AsyncClient
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.models import JobPosting
from app.logging import get_logger
from app.search.meili import with_retries
from app.sync.document import to_meili_doc

LOCK_KEY = "sync:jobs:lock"
WATERMARK_KEY = "sync:jobs:watermark"

logger = get_logger(__name__)


async def acquire_lock(redis: Redis, ttl_seconds: int) -> bool:
    """Try to acquire an exclusive sync lock in Redis. Returns True on success."""
    ok = await redis.set(LOCK_KEY, "1", nx=True, ex=ttl_seconds)
    return bool(ok)


async def release_lock(redis: Redis) -> None:
    await redis.delete(LOCK_KEY)


async def get_watermark(redis: Redis) -> datetime:
    raw = await redis.get(WATERMARK_KEY)
    if not raw:
        return datetime.fromtimestamp(0, tz=UTC)
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return datetime.fromtimestamp(0, tz=UTC)


async def set_watermark(redis: Redis, value: datetime) -> None:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    await redis.set(WATERMARK_KEY, value.isoformat())


async def _push_batch(
    client: AsyncClient,
    index_name: str,
    docs: list[dict],
    batch_size: int,
) -> None:
    if not docs:
        return
    index = client.index(index_name)

    async def _op() -> None:
        tasks = await index.add_documents_in_batches(
            docs, batch_size=batch_size, primary_key="id"
        )
        for t in tasks:
            await client.wait_for_task(t.task_uid, timeout_in_ms=120_000)

    await with_retries(f"meili.add_documents({len(docs)} docs)", _op)


async def full_reindex(
    session: AsyncSession,
    meili: AsyncClient,
    redis: Redis,
    settings: Settings | None = None,
) -> dict[str, int]:
    """Re-push every row from Postgres to Meilisearch.

    Returns counters for observability. Watermark advances to the maximum
    ``updated_at`` seen so subsequent incremental runs pick up from there.
    """
    settings = settings or get_settings()

    if not await acquire_lock(redis, settings.sync_lock_ttl):
        logger.warning("sync.full.lock_held")
        return {"pushed": 0, "skipped": 1}

    try:
        batch: list[dict] = []
        total = 0
        max_updated_at: datetime | None = None

        stmt = select(JobPosting).order_by(JobPosting.id).execution_options(yield_per=500)
        result = await session.stream_scalars(stmt)

        async for row in result:
            batch.append(to_meili_doc(row, settings.sync_description_max_bytes))
            total += 1
            if max_updated_at is None or row.updated_at > max_updated_at:
                max_updated_at = row.updated_at

            if len(batch) >= settings.sync_batch_size:
                await _push_batch(
                    meili, settings.meili_index_name, batch, settings.sync_batch_size
                )
                batch = []

        if batch:
            await _push_batch(
                meili, settings.meili_index_name, batch, settings.sync_batch_size
            )

        if max_updated_at is not None:
            await set_watermark(redis, max_updated_at)

        logger.info("sync.full.done", pushed=total)
        return {"pushed": total, "skipped": 0}
    finally:
        await release_lock(redis)


async def incremental_reindex(
    session: AsyncSession,
    meili: AsyncClient,
    redis: Redis,
    settings: Settings | None = None,
) -> dict[str, int]:
    """Push rows whose ``updated_at`` has advanced past the stored watermark."""
    settings = settings or get_settings()

    if not await acquire_lock(redis, settings.sync_lock_ttl):
        logger.warning("sync.incremental.lock_held")
        return {"pushed": 0, "skipped": 1}

    try:
        watermark = await get_watermark(redis)
        logger.info("sync.incremental.start", watermark=watermark.isoformat())

        batch: list[dict] = []
        total = 0
        max_updated_at: datetime | None = None

        stmt = (
            select(JobPosting)
            .where(JobPosting.updated_at > watermark)
            .order_by(JobPosting.updated_at, JobPosting.id)
            .execution_options(yield_per=500)
        )
        result = await session.stream_scalars(stmt)

        async for row in result:
            batch.append(to_meili_doc(row, settings.sync_description_max_bytes))
            total += 1
            if max_updated_at is None or row.updated_at > max_updated_at:
                max_updated_at = row.updated_at

            if len(batch) >= settings.sync_batch_size:
                await _push_batch(
                    meili, settings.meili_index_name, batch, settings.sync_batch_size
                )
                batch = []

        if batch:
            await _push_batch(
                meili, settings.meili_index_name, batch, settings.sync_batch_size
            )

        if max_updated_at is not None:
            await set_watermark(redis, max_updated_at)

        logger.info("sync.incremental.done", pushed=total)
        return {"pushed": total, "skipped": 0}
    finally:
        await release_lock(redis)
