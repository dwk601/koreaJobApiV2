"""Meilisearch async client factory + lightweight retry wrapper.

Construction is lazy so tests can override the URL/key via env before the
first call. Retry wrapper handles transient 5xx / connection errors with a
small exponential backoff.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import httpx
from meilisearch_python_sdk import AsyncClient
from meilisearch_python_sdk.errors import MeilisearchCommunicationError

from app.config import Settings, get_settings
from app.logging import get_logger

_client: AsyncClient | None = None
_logger = get_logger(__name__)


def get_meili_client(settings: Settings | None = None) -> AsyncClient:
    """Return the module-level ``AsyncClient``, constructing it on first call."""
    global _client
    if _client is None:
        settings = settings or get_settings()
        timeout_seconds = max(1, settings.meili_timeout_ms // 1000)
        _client = AsyncClient(
            url=settings.meili_url,
            api_key=settings.meili_master_key or None,
            timeout=timeout_seconds,
        )
    return _client


async def close_meili() -> None:
    """Close the shared httpx transport and reset module state."""
    global _client
    if _client is not None:
        await _client.aclose()
    _client = None


def reset_meili_for_tests() -> None:
    global _client
    _client = None


async def with_retries[T](
    op_name: str,
    func: Callable[[], Awaitable[T]],
    *,
    tries: int = 3,
    base_delay: float = 0.25,
) -> T:
    """Run ``func`` up to ``tries`` times with exponential backoff.

    Retries on Meili communication errors and 5xx responses. Non-retryable
    errors (4xx, application errors) are re-raised immediately.
    """
    last_exc: Exception | None = None
    for attempt in range(1, tries + 1):
        try:
            return await func()
        except MeilisearchCommunicationError as exc:
            last_exc = exc
        except httpx.HTTPStatusError as exc:
            last_exc = exc
            if exc.response.status_code < 500:
                raise
        except httpx.HTTPError as exc:
            # Generic transient network issue (timeout, read, connect).
            last_exc = exc

        if attempt < tries:
            delay = base_delay * (2 ** (attempt - 1))
            _logger.warning(
                "meili.retry",
                op=op_name,
                attempt=attempt,
                next_delay_s=delay,
                error=str(last_exc),
            )
            await asyncio.sleep(delay)

    assert last_exc is not None
    raise last_exc
