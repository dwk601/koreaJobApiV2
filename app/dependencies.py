"""FastAPI dependency wiring."""
from __future__ import annotations

from fastapi import Depends
from meilisearch_python_sdk import AsyncClient as MeiliClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.redis_client import get_redis
from app.db.engine import get_session
from app.search.meili import get_meili_client
from app.services.job_service import JobService


async def get_meili() -> MeiliClient:
    return get_meili_client()


async def get_cache() -> Redis:
    return get_redis()


async def get_job_service(
    session: AsyncSession = Depends(get_session),
    meili: MeiliClient = Depends(get_meili),
    cache: Redis = Depends(get_cache),
) -> JobService:
    return JobService(session=session, meili=meili, cache=cache)
