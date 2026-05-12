"""IP sliding-window rate limiter backed by Redis sorted sets.

Algorithm (per PLAN §Task 10)
-----------------------------
For each request that isn't on the skip list we compute:

* **bucket** — route class: ``list`` (``/api/v1/jobs``, ``/api/v1/jobs/facets``),
  ``suggest`` (``/api/v1/jobs/suggest``), otherwise ``default``.
* **client** — first IP from ``X-Forwarded-For`` if present, else
  ``request.client.host``.

The Redis key ``rl:{bucket}:{client}`` is a sorted set where each member
represents one request and the score is the request's millisecond timestamp.

On every request we::

    ZREMRANGEBYSCORE key 0 (now - 60_000)   # drop anything older than 60s
    ZCARD key                               # count live requests

If the count is already ``>= limit`` we reject with ``429`` and a
``Retry-After`` equal to ``ceil((oldest_score + 60_000 - now) / 1000)``.

Otherwise we::

    ZADD key <now> "<now>-<rand>"
    EXPIRE key 61

Responses always carry ``X-RateLimit-Limit``, ``X-RateLimit-Remaining`` and
``X-RateLimit-Reset`` (seconds until at least one slot frees).

Failure mode
------------
If Redis is unreachable (connection refused, timeout, any ``RedisError``)
we log a warning and **fail open** — the request passes through without
rate-limit headers. That matches the graceful-degradation contract in
PLAN §9.
"""
from __future__ import annotations

import os
import time
from collections.abc import Awaitable, Callable

from redis.asyncio import Redis
from redis.exceptions import RedisError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.cache.redis_client import get_redis
from app.config import Settings, get_settings
from app.logging import get_logger

_logger = get_logger(__name__)

# Exact paths that never consume a rate-limit slot.
SKIP_PATHS: frozenset[str] = frozenset(
    {"/health", "/ready", "/docs", "/openapi.json", "/redoc"}
)

# Paths that route to the "list" bucket. These share a single cap since
# the UI usually fires both in tandem.
_LIST_PATHS: frozenset[str] = frozenset(
    {"/api/v1/jobs", "/api/v1/jobs/", "/api/v1/jobs/facets", "/api/v1/jobs/facets/"}
)
_SUGGEST_PATHS: frozenset[str] = frozenset(
    {"/api/v1/jobs/suggest", "/api/v1/jobs/suggest/"}
)


def should_skip(path: str) -> bool:
    """Return True for paths that bypass the rate limiter entirely.

    Exact matches on :data:`SKIP_PATHS` plus any Swagger-UI sub-asset
    (``/docs/oauth2-redirect`` etc.).
    """
    if path in SKIP_PATHS:
        return True
    return path.startswith("/docs/")


def classify_bucket(path: str, settings: Settings) -> tuple[str, int]:
    """Map a request path to ``(bucket_name, limit_per_minute)``."""
    if path in _LIST_PATHS:
        return "list", settings.rate_limit_list_per_min
    if path in _SUGGEST_PATHS:
        return "suggest", settings.rate_limit_suggest_per_min
    return "default", settings.rate_limit_default_per_min


def resolve_client_key(request: Request) -> str:
    """Extract a stable client identity from the request.

    Priority:
    1. First entry of ``X-Forwarded-For`` (trusted upstream proxy).
    2. ``request.client.host``.
    3. The literal ``"unknown"`` (keeps the key space bounded).
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",", 1)[0].strip()
        if first:
            return first
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


_WINDOW_MS = 60_000
_EXPIRE_SECONDS = 61  # one second of slack so the key outlives its newest entry


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-IP sliding-window rate limit middleware.

    Parameters
    ----------
    app:
        Downstream ASGI app (Starlette passes this in automatically when
        registered via ``add_middleware``).
    settings:
        Optional override for tests. Defaults to the cached
        :func:`get_settings` instance at each request.
    redis_factory:
        Optional override returning a :class:`redis.asyncio.Redis` client.
        Defaults to the lazy module-level pool in
        :func:`app.cache.redis_client.get_redis`.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        settings: Settings | None = None,
        redis_factory: Callable[[], Redis] | None = None,
    ) -> None:
        super().__init__(app)
        self._settings_override = settings
        self._redis_factory = redis_factory or get_redis

    # ── helpers ──

    @property
    def settings(self) -> Settings:
        return self._settings_override or get_settings()

    def _redis(self) -> Redis:
        return self._redis_factory()

    @staticmethod
    def _apply_headers(
        response: Response, *, limit: int, remaining: int, reset: int
    ) -> None:
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, remaining))
        response.headers["X-RateLimit-Reset"] = str(max(0, reset))

    @staticmethod
    def _too_many_requests(
        *, limit: int, retry_after: int, bucket: str
    ) -> JSONResponse:
        response = JSONResponse(
            status_code=429,
            content={
                "error": {
                    "code": "RATE_LIMITED",
                    "message": "Too many requests",
                    "detail": {
                        "bucket": bucket,
                        "limit": limit,
                        "window_seconds": 60,
                    },
                }
            },
        )
        response.headers["Retry-After"] = str(retry_after)
        return response

    # ── dispatch ──

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        settings = self.settings
        if not settings.rate_limit_enabled:
            return await call_next(request)

        path = request.url.path
        if should_skip(path):
            return await call_next(request)

        bucket, limit = classify_bucket(path, settings)
        client = resolve_client_key(request)
        key = f"rl:{bucket}:{client}"
        now_ms = int(time.time() * 1000)
        window_floor = now_ms - _WINDOW_MS

        redis = self._redis()

        # ── Prune stale entries and read current count ──
        try:
            async with redis.pipeline(transaction=False) as pipe:
                pipe.zremrangebyscore(key, 0, window_floor)
                pipe.zcard(key)
                _, current = await pipe.execute()
        except (RedisError, OSError) as exc:
            _logger.warning(
                "ratelimit.redis.error",
                phase="prune",
                bucket=bucket,
                client=client,
                error=str(exc),
            )
            return await call_next(request)  # fail open

        current = int(current or 0)

        # ── Over the cap → 429 ──
        if current >= limit:
            retry_after = 1
            try:
                oldest = await redis.zrange(key, 0, 0, withscores=True)
                if oldest:
                    # `oldest` is list[tuple[bytes|str, float]]
                    oldest_score = int(oldest[0][1])
                    remaining_ms = oldest_score + _WINDOW_MS - now_ms
                    if remaining_ms > 0:
                        # ceil to whole seconds; at least 1s
                        retry_after = max(1, (remaining_ms + 999) // 1000)
            except (RedisError, OSError) as exc:
                _logger.warning(
                    "ratelimit.redis.error",
                    phase="retry_after",
                    bucket=bucket,
                    client=client,
                    error=str(exc),
                )

            response = self._too_many_requests(
                limit=limit, retry_after=retry_after, bucket=bucket
            )
            self._apply_headers(
                response, limit=limit, remaining=0, reset=retry_after
            )
            return response

        # ── Under the cap → record this request and pass through ──
        # A random suffix avoids score-collisions when multiple requests
        # land within the same millisecond from the same client.
        member = f"{now_ms}-{os.urandom(4).hex()}"
        try:
            async with redis.pipeline(transaction=False) as pipe:
                pipe.zadd(key, {member: now_ms})
                pipe.expire(key, _EXPIRE_SECONDS)
                await pipe.execute()
        except (RedisError, OSError) as exc:
            _logger.warning(
                "ratelimit.redis.error",
                phase="register",
                bucket=bucket,
                client=client,
                error=str(exc),
            )
            return await call_next(request)  # fail open

        new_count = current + 1
        remaining = limit - new_count
        # Reset = seconds until at least one slot frees. For the first
        # request of a window that's the full 60s; for subsequent requests
        # it's the age-out of this very request (still 60s since we just
        # added it) — i.e. the window is always 60s for a sliding log.
        reset_seconds = 60

        response = await call_next(request)
        self._apply_headers(
            response, limit=limit, remaining=remaining, reset=reset_seconds
        )
        return response
