"""FastAPI application factory.

Usage:
    uv run fastapi dev app/main.py        # dev
    uv run uvicorn app.main:app --reload  # alt
    exec uvicorn app.main:app             # prod (see scripts/entrypoint.sh)
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.cache.redis_client import close_redis
from app.config import Settings, get_settings
from app.db.engine import close_engine
from app.exceptions import register_exception_handlers
from app.logging import configure_logging, get_logger
from app.middleware import RateLimitMiddleware, RequestIDMiddleware
from app.routers import health as health_router
from app.routers import jobs as jobs_router
from app.search.meili import close_meili


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup/shutdown hooks.

    Task 1 scope is minimal (log that we're up). Later tasks attach
    Redis pool, Meilisearch client, and DB engine teardown here.
    """
    logger = get_logger(__name__)
    settings: Settings = get_settings()
    logger.info(
        "app.startup",
        app_name=settings.app_name,
        debug=settings.debug,
        log_level=settings.log_level,
    )
    try:
        yield
    finally:
        await close_meili()
        await close_redis()
        await close_engine()
        logger.info("app.shutdown")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure the FastAPI app.

    Parameters
    ----------
    settings:
        Optional override (useful for tests). Defaults to the cached
        ``get_settings()`` instance.
    """
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title=settings.app_name,
        debug=settings.debug,
        lifespan=lifespan,
    )

    # ── Middleware stack ──
    # Starlette applies the LAST-added middleware OUTERMOST, so the
    # registration order below is: innermost → outermost.
    #
    #   Request flow:   RequestID → CORS → RateLimit → router
    #   Response flow:  router    → RateLimit → CORS → RequestID
    #
    # Rationale:
    # * RequestID wraps everything so request.start / request.end access
    #   logs are emitted even when an inner layer returns 429 or 5xx, and
    #   every log line during the request carries ``request_id``.
    # * CORS sits outside the rate limiter so 429 responses still receive
    #   the Access-Control-* headers browsers expect, and OPTIONS preflight
    #   requests are answered before any bucket is consumed.
    # * Rate limit is innermost — it can short-circuit with 429 before the
    #   request reaches a router.

    # (1) innermost: rate limit
    app.add_middleware(RateLimitMiddleware)

    # (2) middle: CORS
    origins = settings.cors_origins
    allow_all = origins == ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if allow_all else origins,
        allow_credentials=not allow_all,
        allow_methods=["GET", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=[
            "X-Request-ID",
            "X-RateLimit-Limit",
            "X-RateLimit-Remaining",
            "X-RateLimit-Reset",
        ],
    )

    # (3) outermost: request-id + access log
    app.add_middleware(RequestIDMiddleware)

    # Routers
    app.include_router(health_router.router)
    app.include_router(jobs_router.router)

    # Exception handlers (common error envelope)
    register_exception_handlers(app)

    return app


app = create_app()
