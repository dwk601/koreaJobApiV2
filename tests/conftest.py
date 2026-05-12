"""Shared pytest fixtures.

Integration tests spin up real services via testcontainers (Postgres for
now; Redis + Meili added in later tasks). They're marked with
``@pytest.mark.integration`` so unit tests can run without Docker.

Skipped automatically when Docker is unavailable on the host.
"""
from __future__ import annotations

import asyncio
import contextlib
import shutil
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def _docker_available() -> bool:
    return shutil.which("docker") is not None


async def _apply_schema(async_dsn: str, sql: str) -> None:
    import asyncpg  # runtime dep

    # asyncpg wants a plain postgres:// DSN, not the SQLAlchemy variant.
    plain_dsn = async_dsn.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(plain_dsn)
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def pg_container() -> Iterator[object]:
    """Session-scoped Postgres 17 container with the ETL schema applied."""
    if not _docker_available():
        pytest.skip("Docker is not available")
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer(
        image="postgres:17-alpine",
        username="dev",
        password="devpassword",
        dbname="job",
        driver="asyncpg",
    ) as pg:
        schema_sql = (FIXTURES / "etl_schema.sql").read_text(encoding="utf-8")
        async_dsn: str = pg.get_connection_url()
        asyncio.run(_apply_schema(async_dsn, schema_sql))
        yield pg


@pytest.fixture
def pg_dsn_async(pg_container: object) -> str:
    """Async SQLAlchemy DSN pointing at the test container."""
    return pg_container.get_connection_url()  # type: ignore[attr-defined]


@pytest.fixture
async def pg_engine(
    pg_dsn_async: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[object]:
    """Yield an AsyncEngine pointed at the test container; reset module state."""
    from app.config import get_settings
    from app.db import engine as eng_module

    monkeypatch.setenv("DATABASE_URL", pg_dsn_async)
    get_settings.cache_clear()
    eng_module.reset_engine_for_tests()

    engine = eng_module.get_engine()
    try:
        yield engine
    finally:
        await engine.dispose()
        eng_module.reset_engine_for_tests()
        get_settings.cache_clear()


@pytest.fixture(scope="session")
def meili_container() -> Iterator[object]:
    """Session-scoped Meilisearch v1.x container."""
    if not _docker_available():
        pytest.skip("Docker is not available")
    from testcontainers.core.container import DockerContainer
    from testcontainers.core.waiting_utils import wait_for_logs

    container = DockerContainer("getmeili/meilisearch:v1.10")
    container.with_env("MEILI_MASTER_KEY", "testMasterKey")
    container.with_env("MEILI_NO_ANALYTICS", "true")
    container.with_env("MEILI_ENV", "development")
    container.with_exposed_ports(7700)
    container.start()
    try:
        wait_for_logs(container, "Server listening on", timeout=30)
        yield container
    finally:
        container.stop()


@pytest.fixture
def meili_url(meili_container: object) -> str:
    host = meili_container.get_container_host_ip()  # type: ignore[attr-defined]
    port = meili_container.get_exposed_port(7700)  # type: ignore[attr-defined]
    return f"http://{host}:{port}"


@pytest.fixture
async def meili_env(
    meili_url: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[str]:
    """Monkeypatch MEILI_* env vars and reset the module-level client."""
    from app.config import get_settings
    from app.search import meili as meili_module

    monkeypatch.setenv("MEILI_URL", meili_url)
    monkeypatch.setenv("MEILI_MASTER_KEY", "testMasterKey")
    # Force a unique index per test-session run so state doesn't bleed
    # between test files; the session-scoped container is reused.
    monkeypatch.setenv("MEILI_INDEX_NAME", "jobs_test")
    get_settings.cache_clear()
    meili_module.reset_meili_for_tests()
    try:
        yield meili_url
    finally:
        # Clean up the index so the next test starts fresh.
        from meilisearch_python_sdk import AsyncClient

        async with AsyncClient(url=meili_url, api_key="testMasterKey") as client:
            with contextlib.suppress(Exception):
                await client.delete_index_if_exists("jobs_test")
        await meili_module.close_meili()
        meili_module.reset_meili_for_tests()
        get_settings.cache_clear()


@pytest.fixture(scope="session")
def redis_container() -> Iterator[object]:
    """Session-scoped Redis 7 container."""
    if not _docker_available():
        pytest.skip("Docker is not available")
    from testcontainers.redis import RedisContainer

    with RedisContainer(image="redis:7-alpine") as rc:
        yield rc


@pytest.fixture
def redis_url(redis_container: object) -> str:
    host = redis_container.get_container_host_ip()  # type: ignore[attr-defined]
    port = redis_container.get_exposed_port(6379)  # type: ignore[attr-defined]
    return f"redis://{host}:{port}/0"


@pytest.fixture
async def redis_env(
    redis_url: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[str]:
    """Monkeypatch REDIS_URL and reset the module-level client.

    Also flushes the DB at setup so each test sees a clean slate.
    """
    from app.cache import redis_client as rc_module
    from app.config import get_settings

    monkeypatch.setenv("REDIS_URL", redis_url)
    get_settings.cache_clear()
    rc_module.reset_redis_for_tests()

    # Clean slate.
    redis = rc_module.get_redis()
    await redis.flushdb()

    try:
        yield redis_url
    finally:
        await rc_module.close_redis()
        rc_module.reset_redis_for_tests()
        get_settings.cache_clear()
