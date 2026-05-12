"""Unit tests for `app.middleware.request_id` — sanitiser + middleware."""
from __future__ import annotations

import uuid

import pytest
import structlog
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.middleware.request_id import (
    HEADER_NAME,
    RequestIDMiddleware,
    generate_request_id,
    sanitize_incoming,
)

# ─────────────────────────── sanitize_incoming ───────────────────────────


@pytest.mark.parametrize(
    "value",
    [
        "abc123",
        "550e8400-e29b-41d4-a716-446655440000",
        "trace:ab_c-1.2",
        "A" * 128,
    ],
)
def test_sanitize_incoming_accepts_safe_values(value: str) -> None:
    assert sanitize_incoming(value) == value


def test_sanitize_incoming_strips_surrounding_whitespace() -> None:
    assert sanitize_incoming("  abc  ") == "abc"


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "   ",
        "has space",
        "has\ttab",
        "new\nline",
        "quotes\"inside",
        "A" * 129,  # too long
    ],
)
def test_sanitize_incoming_rejects_invalid_values(value: str | None) -> None:
    assert sanitize_incoming(value) is None


# ─────────────────────────── generate_request_id ───────────────────────────


def test_generate_request_id_is_uuid4_string() -> None:
    rid = generate_request_id()
    parsed = uuid.UUID(rid)
    assert parsed.version == 4


def test_generate_request_id_unique_per_call() -> None:
    a, b = generate_request_id(), generate_request_id()
    assert a != b


# ─────────────────────────── middleware behaviour ───────────────────────────


def _build_probe_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)

    @app.get("/echo")
    async def _echo() -> dict[str, object]:
        # Reflect whatever structlog currently has bound so tests can assert
        # that the middleware bound the id before the handler ran.
        bound = structlog.contextvars.get_contextvars()
        return {"bound": bound}

    @app.get("/boom")
    async def _boom() -> None:
        raise RuntimeError("boom")

    return app


@pytest.fixture
async def probe_client() -> AsyncClient:
    transport = ASGITransport(app=_build_probe_app(), raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


async def test_generates_request_id_when_header_absent(
    probe_client: AsyncClient,
) -> None:
    r = await probe_client.get("/echo")
    assert r.status_code == 200
    echoed = r.headers[HEADER_NAME]
    # UUID4 → parse should succeed.
    parsed = uuid.UUID(echoed)
    assert parsed.version == 4
    assert r.json()["bound"]["request_id"] == echoed


async def test_passes_through_valid_incoming_header(
    probe_client: AsyncClient,
) -> None:
    r = await probe_client.get("/echo", headers={HEADER_NAME: "my-trace-abc"})
    assert r.headers[HEADER_NAME] == "my-trace-abc"
    assert r.json()["bound"]["request_id"] == "my-trace-abc"


async def test_replaces_malformed_incoming_header(
    probe_client: AsyncClient,
) -> None:
    # A header with whitespace would fail the regex → the middleware must
    # discard it and generate a fresh UUID4 instead.
    r = await probe_client.get(
        "/echo", headers={HEADER_NAME: "has space"}
    )
    new_id = r.headers[HEADER_NAME]
    assert new_id != "has space"
    uuid.UUID(new_id)  # Must parse.


async def test_context_is_cleared_between_requests(
    probe_client: AsyncClient,
) -> None:
    r1 = await probe_client.get("/echo")
    r2 = await probe_client.get("/echo")
    id1 = r1.json()["bound"]["request_id"]
    id2 = r2.json()["bound"]["request_id"]
    assert id1 != id2
    # After both calls, no leaked binding survives outside the middleware.
    assert "request_id" not in structlog.contextvars.get_contextvars()


async def test_context_is_cleared_after_unhandled_exception(
    probe_client: AsyncClient,
) -> None:
    # raise_app_exceptions=False on the transport → Starlette returns 500.
    r = await probe_client.get("/boom")
    assert r.status_code == 500
    # Binding must be cleared even when the downstream raised.
    assert "request_id" not in structlog.contextvars.get_contextvars()
