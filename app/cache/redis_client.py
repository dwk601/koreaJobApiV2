"""Async Redis client.

Lazy module-level connection pool constructed from ``Settings`` on first
call. Keep the surface area minimal here — read-through caching helpers
are added in Task 9; rate-limiting uses the same pool in Task 10.
"""
from __future__ import annotations

from redis.asyncio import ConnectionPool, Redis

from app.config import Settings, get_settings

_pool: ConnectionPool | None = None
_redis: Redis | None = None


def get_redis(settings: Settings | None = None) -> Redis:
    """Return the shared ``Redis`` client, constructing it on first call."""
    global _pool, _redis
    if _redis is None:
        settings = settings or get_settings()
        _pool = ConnectionPool.from_url(
            settings.redis_url,
            max_connections=settings.redis_max_connections,
            socket_timeout=settings.redis_socket_timeout,
            socket_connect_timeout=settings.redis_socket_connect_timeout,
            decode_responses=True,
        )
        _redis = Redis(connection_pool=_pool)
    return _redis


async def close_redis() -> None:
    """Release the pool. Safe to call multiple times."""
    global _pool, _redis
    if _redis is not None:
        await _redis.aclose()
    if _pool is not None:
        await _pool.aclose()
    _redis = None
    _pool = None


def reset_redis_for_tests() -> None:
    global _pool, _redis
    _pool = None
    _redis = None
