"""Unit tests for `app.exceptions` — exception classes + handler bodies."""
from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, Field

from app.exceptions import (
    NotFound,
    UpstreamUnavailable,
    ValidationFailed,
    error_envelope,
    register_exception_handlers,
)

# ─────────────────────────── exception classes ───────────────────────────


def test_not_found_defaults() -> None:
    exc = NotFound()
    assert exc.code == "NOT_FOUND"
    assert exc.status_code == 404
    assert exc.message == "Resource not found"
    assert exc.detail == {}


def test_not_found_with_custom_message_and_detail() -> None:
    exc = NotFound("Job not found", {"id": 42})
    assert exc.message == "Job not found"
    assert exc.detail == {"id": 42}


def test_validation_failed_defaults() -> None:
    exc = ValidationFailed()
    assert exc.code == "VALIDATION_FAILED"
    assert exc.status_code == 422
    assert exc.detail == {}


def test_upstream_unavailable_records_component() -> None:
    exc = UpstreamUnavailable("redis")
    assert exc.code == "UPSTREAM_UNAVAILABLE"
    assert exc.status_code == 503
    assert exc.component == "redis"
    assert exc.detail == {"component": "redis"}


def test_upstream_unavailable_merges_extra_detail() -> None:
    exc = UpstreamUnavailable(
        "postgres", "PG is offline", detail={"retries": 3}
    )
    assert exc.message == "PG is offline"
    assert exc.detail == {"component": "postgres", "retries": 3}


def test_error_envelope_shape() -> None:
    env = error_envelope("NOT_FOUND", "missing", {"id": 1})
    assert env == {
        "error": {"code": "NOT_FOUND", "message": "missing", "detail": {"id": 1}}
    }


def test_error_envelope_defaults_to_empty_detail() -> None:
    env = error_envelope("X", "y")
    assert env["error"]["detail"] == {}


# ─────────────────────────── handlers (end-to-end, in-memory) ───────────────────────────


def _make_app() -> FastAPI:
    """Minimal app that exercises every handler path."""
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/raise-not-found")
    async def _nf() -> None:
        raise NotFound("Job not found", {"id": 999})

    @app.get("/raise-validation")
    async def _vf() -> None:
        raise ValidationFailed("bad cursor", {"cursor": "xx"})

    @app.get("/raise-upstream")
    async def _up() -> None:
        raise UpstreamUnavailable("meilisearch")

    @app.get("/raise-http")
    async def _http() -> None:
        raise HTTPException(status_code=409, detail="conflict")

    class _Body(BaseModel):
        n: int = Field(ge=1)

    @app.get("/validate-query")
    async def _q(body: _Body) -> dict[str, int]:  # Pydantic query validation
        return {"n": body.n}

    @app.get("/force-validation-error")
    async def _fve() -> None:
        raise RequestValidationError(
            errors=[
                {
                    "type": "missing",
                    "loc": ("query", "x"),
                    "msg": "field required",
                    "input": None,
                }
            ]
        )

    return app


@pytest.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=_make_app())
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


async def test_handler_not_found_envelope(client: AsyncClient) -> None:
    r = await client.get("/raise-not-found")
    assert r.status_code == 404
    assert r.json() == {
        "error": {
            "code": "NOT_FOUND",
            "message": "Job not found",
            "detail": {"id": 999},
        }
    }


async def test_handler_validation_failed_envelope(client: AsyncClient) -> None:
    r = await client.get("/raise-validation")
    assert r.status_code == 422
    assert r.json() == {
        "error": {
            "code": "VALIDATION_FAILED",
            "message": "bad cursor",
            "detail": {"cursor": "xx"},
        }
    }


async def test_handler_upstream_unavailable_envelope(client: AsyncClient) -> None:
    r = await client.get("/raise-upstream")
    assert r.status_code == 503
    body = r.json()
    assert body["error"]["code"] == "UPSTREAM_UNAVAILABLE"
    assert body["error"]["detail"]["component"] == "meilisearch"


async def test_handler_http_exception_wraps_in_envelope(client: AsyncClient) -> None:
    r = await client.get("/raise-http")
    assert r.status_code == 409
    assert r.json() == {
        "error": {
            "code": "CONFLICT",
            "message": "conflict",
            "detail": {},
        }
    }


async def test_unknown_route_returns_404_envelope(client: AsyncClient) -> None:
    r = await client.get("/definitely-not-a-route")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


async def test_wrong_method_returns_405_envelope(client: AsyncClient) -> None:
    r = await client.post("/raise-not-found")
    assert r.status_code == 405
    assert r.json()["error"]["code"] == "METHOD_NOT_ALLOWED"


async def test_forced_request_validation_error_has_envelope(client: AsyncClient) -> None:
    r = await client.get("/force-validation-error")
    assert r.status_code == 422
    body = r.json()
    assert body["error"]["code"] == "VALIDATION_FAILED"
    assert body["error"]["message"] == "Validation failed"
    assert body["error"]["detail"]["errors"][0]["loc"] == ["query", "x"]
    assert body["error"]["detail"]["errors"][0]["type"] == "missing"


async def test_fastapi_query_validation_renders_envelope(client: AsyncClient) -> None:
    # Pydantic constraint (ge=1) fails → FastAPI raises RequestValidationError
    r = await client.get("/validate-query", params={"n": 0})
    assert r.status_code == 422
    body = r.json()
    assert body["error"]["code"] == "VALIDATION_FAILED"
    # FastAPI nests the error list under detail.errors.
    errors = body["error"]["detail"]["errors"]
    assert errors and "loc" in errors[0] and "msg" in errors[0]
