"""Liveness and readiness endpoints.

``/health`` is liveness-only: it succeeds whenever the process can respond.
``/ready`` probes every required upstream — Postgres, Redis, Meilisearch —
in parallel via :func:`asyncio.gather` with ``return_exceptions=True``. The
probe returns ``200`` only when every component is OK, otherwise ``503``
with a per-component status map so orchestrators can pinpoint the failure.

Response shape::

    {"status": "ok" | "degraded",
     "components": {"postgres": "ok|unhealthy",
                    "redis":    "ok|unhealthy",
                    "meilisearch": "ok|unhealthy"}}
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.cache.redis_client import get_redis
from app.db.engine import get_sessionmaker
from app.logging import get_logger
from app.search.meili import get_meili_client

router = APIRouter(tags=["health"])
_logger = get_logger(__name__)


@router.get("/health", summary="Liveness probe")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# ─────────────────────────── /ready ───────────────────────────


async def _check_postgres() -> str:
    sm = get_sessionmaker()
    async with sm() as session:
        await session.execute(text("SELECT 1"))
    return "ok"


async def _check_redis() -> str:
    redis = get_redis()
    pong = await redis.ping()
    if not pong:
        raise RuntimeError("redis PING returned falsy")
    return "ok"


async def _check_meili() -> str:
    client = get_meili_client()
    # ``health()`` raises on connection/HTTP errors; otherwise returns a
    # status dict like ``{"status": "available"}``.
    status = await client.health()
    reported = getattr(status, "status", None) or (
        status.get("status") if isinstance(status, dict) else None
    )
    if reported and str(reported).lower() not in {"available", "ok"}:
        raise RuntimeError(f"meilisearch reported status={reported!r}")
    return "ok"


_COMPONENTS: tuple[tuple[str, Any], ...] = (
    ("postgres", _check_postgres),
    ("redis", _check_redis),
    ("meilisearch", _check_meili),
)


@router.get(
    "/ready",
    summary="Readiness probe (PG + Redis + Meili)",
    responses={
        200: {"description": "All upstreams healthy"},
        503: {"description": "At least one upstream unhealthy"},
    },
)
async def ready() -> JSONResponse:
    names = [name for name, _ in _COMPONENTS]
    results = await asyncio.gather(
        *(_run_check(name, fn) for name, fn in _COMPONENTS),
        return_exceptions=True,
    )

    components: dict[str, str] = {}
    unhealthy: dict[str, str] = {}
    for name, result in zip(names, results, strict=True):
        if isinstance(result, BaseException):
            components[name] = "unhealthy"
            unhealthy[name] = repr(result)
        else:
            components[name] = result

    all_ok = all(v == "ok" for v in components.values())
    body: dict[str, Any] = {
        "status": "ok" if all_ok else "degraded",
        "components": components,
    }
    if all_ok:
        return JSONResponse(status_code=200, content=body)

    _logger.warning("ready.degraded", components=components, errors=unhealthy)
    return JSONResponse(status_code=503, content=body)


async def _run_check(name: str, fn: Any) -> str:
    """Wrap an individual check so a single-component exception doesn't
    cancel the others (``gather(return_exceptions=True)`` handles the
    timing — this wrapper only exists to attach the component name to
    any logged error)."""
    try:
        return await fn()
    except Exception as exc:
        _logger.info("ready.check.failed", component=name, error=str(exc))
        raise
