"""Read-through cache helper with graceful Redis degradation.

Contract::

    value = await get_or_set(
        redis, key, ttl,
        loader=<async callable returning T>,
        serialize=<T -> str>,
        deserialize=<str -> T>,
    )

* A cache hit returns the deserialised value.
* A cache miss calls ``loader``, serialises the result, writes it with
  ``ttl`` seconds, and returns the value.
* Any Redis failure (connection / timeout / any ``RedisError``) is logged
  at WARN and treated as a miss — the loader is still called and the
  caller gets a correct answer, just slower.
* If the loader raises, the exception propagates and nothing is cached.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.logging import get_logger

_logger = get_logger(__name__)


async def get_or_set[T](
    redis: Redis | None,
    key: str,
    ttl: int,
    loader: Callable[[], Awaitable[T]],
    *,
    serialize: Callable[[T], str],
    deserialize: Callable[[str], T],
) -> T:
    if redis is None:
        return await loader()

    # GET
    try:
        cached = await redis.get(key)
    except (RedisError, OSError) as exc:
        _logger.warning("cache.get.error", key=key, error=str(exc))
        return await loader()

    if cached is not None:
        try:
            return deserialize(cached)
        except Exception as exc:  # noqa: BLE001 - corrupted cache is best-effort
            _logger.warning("cache.decode.error", key=key, error=str(exc))

    # Miss (or corrupted) — compute fresh.
    value = await loader()

    # SET
    try:
        await redis.set(key, serialize(value), ex=ttl)
    except (RedisError, OSError) as exc:
        _logger.warning("cache.set.error", key=key, error=str(exc))

    return value
