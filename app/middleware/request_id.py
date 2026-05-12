"""Request-ID ingress / egress + structlog binding.

Per request we:

1. Read ``X-Request-ID`` from the incoming headers if present and
   "looks sane" (alphanumeric + ``-_:.``, 1–128 chars). Otherwise we
   generate a fresh UUID4.
2. Bind ``request_id`` to :mod:`structlog.contextvars` so every log line
   emitted during the request (from our code *or* foreign loggers we
   route through ProcessorFormatter) automatically carries it.
3. Emit ``request.start`` before handing off to the next ASGI app and
   ``request.end`` after the response, with ``method``, ``path``,
   ``status``, and ``latency_ms``.
4. Echo the id on the response as ``X-Request-ID`` so downstream clients
   can correlate logs on their side.

This middleware should be registered as the **outermost** middleware in
:func:`app.main.create_app` so every other layer (CORS, rate-limit,
exception handlers) produces logs already bound to the id.
"""
from __future__ import annotations

import re
import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.logging import get_logger

HEADER_NAME = "X-Request-ID"

# Accept common trace-id shapes (UUIDs, hex spans, short tokens) while
# rejecting anything that could inject into our JSON log records.
_VALID_RE = re.compile(r"^[A-Za-z0-9_\-:.]{1,128}$")


def sanitize_incoming(value: str | None) -> str | None:
    """Return a clean incoming id, or None if the header should be ignored."""
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if not _VALID_RE.match(value):
        return None
    return value


def generate_request_id() -> str:
    """Generate a fresh UUID4 string."""
    return str(uuid.uuid4())


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Outermost middleware — owns request-id lifecycle and access logs."""

    def __init__(self, app: ASGIApp, *, header_name: str = HEADER_NAME) -> None:
        super().__init__(app)
        self._header_name = header_name
        self._logger = get_logger("app.request")

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        incoming = sanitize_incoming(request.headers.get(self._header_name))
        request_id = incoming or generate_request_id()

        # Bind for the lifetime of this request; always clear on exit so
        # the context doesn't leak to the next request on the same task.
        structlog.contextvars.bind_contextvars(request_id=request_id)
        start = time.perf_counter()

        method = request.method
        path = request.url.path
        self._logger.info("request.start", method=method, path=path)

        try:
            response = await call_next(request)
        except Exception:
            latency_ms = _elapsed_ms(start)
            self._logger.exception(
                "request.end",
                method=method,
                path=path,
                status=500,
                latency_ms=latency_ms,
            )
            structlog.contextvars.unbind_contextvars("request_id")
            raise

        latency_ms = _elapsed_ms(start)
        self._logger.info(
            "request.end",
            method=method,
            path=path,
            status=response.status_code,
            latency_ms=latency_ms,
        )
        response.headers[self._header_name] = request_id
        structlog.contextvars.unbind_contextvars("request_id")
        return response


def _elapsed_ms(started_perf: float) -> float:
    return round((time.perf_counter() - started_perf) * 1000, 2)
