"""Domain exceptions and FastAPI handlers.

Every non-2xx response from the API uses the same envelope::

    {"error": {"code": "...", "message": "...", "detail": {...}}}

Covered codes:

* ``NOT_FOUND``            — :class:`NotFound`, HTTP 404
* ``VALIDATION_FAILED``    — :class:`ValidationFailed` or FastAPI's
                             :class:`RequestValidationError`, HTTP 422
* ``UPSTREAM_UNAVAILABLE`` — :class:`UpstreamUnavailable`, HTTP 503
* ``RATE_LIMITED``         — emitted directly by
                             :class:`app.middleware.rate_limit.RateLimitMiddleware`,
                             HTTP 429 (with ``Retry-After`` header)

:func:`register_exception_handlers` also overrides Starlette's default
``HTTPException`` handler so stray ``HTTPException`` raises (including
route-not-found 404s and FastAPI's built-in 405s) render as envelopes.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppError(Exception):
    """Base class for API-originated errors mapped to envelopes."""

    code: str = "INTERNAL_ERROR"
    status_code: int = 500
    message: str = "Internal error"

    def __init__(
        self,
        message: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message or self.message)
        self.message = message or self.message
        self.detail = detail or {}


class NotFound(AppError):  # noqa: N818 — envelope code is "NOT_FOUND"
    code = "NOT_FOUND"
    status_code = 404
    message = "Resource not found"


class ValidationFailed(AppError):  # noqa: N818 — envelope code is "VALIDATION_FAILED"
    code = "VALIDATION_FAILED"
    status_code = 422
    message = "Validation failed"


class UpstreamUnavailable(AppError):  # noqa: N818 — envelope code is "UPSTREAM_UNAVAILABLE"
    """Raised when a required upstream (PG, Redis, Meili) is unreachable.

    Always includes the ``component`` in the envelope detail.
    """

    code = "UPSTREAM_UNAVAILABLE"
    status_code = 503
    message = "Upstream service unavailable"

    def __init__(
        self,
        component: str,
        message: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        merged: dict[str, Any] = {"component": component}
        if detail:
            merged.update(detail)
        super().__init__(message or self.message, merged)
        self.component = component


# ─────────────────────────── Rendering helpers ───────────────────────────


def error_envelope(
    code: str, message: str, detail: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Build the canonical error envelope body."""
    return {"error": {"code": code, "message": message, "detail": detail or {}}}


def _envelope_from_app_error(exc: AppError) -> dict[str, Any]:
    return error_envelope(exc.code, exc.message, exc.detail)


# ─────────────────────────── Handlers ───────────────────────────


async def _app_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    return JSONResponse(
        status_code=exc.status_code, content=_envelope_from_app_error(exc)
    )


async def _validation_error_handler(
    _request: Request, exc: Exception
) -> JSONResponse:
    """Map FastAPI's :class:`RequestValidationError` to the envelope.

    The raw pydantic errors are preserved under ``detail.errors`` so clients
    can surface per-field messages.
    """
    assert isinstance(exc, RequestValidationError)
    # Coerce Pydantic error records into JSON-serialisable primitives —
    # ``input`` can be a pydantic URL or other non-serialisable object.
    raw_errors: list[dict[str, Any]] = []
    for err in exc.errors():
        raw_errors.append(
            {
                "type": err.get("type"),
                "loc": list(err.get("loc", [])),
                "msg": err.get("msg"),
                "input": _jsonable(err.get("input")),
            }
        )
    return JSONResponse(
        status_code=422,
        content=error_envelope(
            "VALIDATION_FAILED", "Validation failed", {"errors": raw_errors}
        ),
    )


async def _http_exception_handler(
    _request: Request, exc: Exception
) -> JSONResponse:
    """Render Starlette/FastAPI ``HTTPException`` raises as envelopes.

    Covers default 404 for unknown routes and 405 for wrong methods. Keeps
    the original status code + detail intact.
    """
    assert isinstance(exc, StarletteHTTPException)
    code = _code_for_status(exc.status_code)
    message = _message_for(exc)
    detail: dict[str, Any] = {}
    if isinstance(exc.detail, dict):
        detail = exc.detail
    elif exc.detail not in (None, message):
        detail = {"reason": exc.detail}
    response = JSONResponse(
        status_code=exc.status_code,
        content=error_envelope(code, message, detail),
    )
    if exc.headers:
        for name, value in exc.headers.items():
            response.headers[name] = value
    return response


def _code_for_status(status: int) -> str:
    return {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        405: "METHOD_NOT_ALLOWED",
        409: "CONFLICT",
        422: "VALIDATION_FAILED",
        429: "RATE_LIMITED",
        503: "UPSTREAM_UNAVAILABLE",
    }.get(status, "HTTP_ERROR")


def _message_for(exc: StarletteHTTPException) -> str:
    if isinstance(exc.detail, str) and exc.detail:
        return exc.detail
    return {
        404: "Resource not found",
        405: "Method not allowed",
        429: "Too many requests",
    }.get(exc.status_code, "HTTP error")


def _jsonable(value: Any) -> Any:
    """Best-effort coerce a Pydantic-error ``input`` into JSON primitives."""
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return str(value)


def register_exception_handlers(app: FastAPI) -> None:
    """Attach handlers for all envelope-producing error classes."""
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
