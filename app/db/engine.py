"""Async SQLAlchemy engine + session management.

The engine and sessionmaker are created lazily on first use so that tests can
override the DSN before construction. Use ``close_engine()`` during app
shutdown to release pool resources cleanly.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings, get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine(settings: Settings | None = None) -> AsyncEngine:
    """Return the module-level async engine, constructing it on first call."""
    global _engine
    if _engine is None:
        settings = settings or get_settings()
        _engine = create_async_engine(
            settings.database_url,
            pool_pre_ping=True,
            future=True,
            echo=False,
        )
    return _engine


def get_sessionmaker(
    settings: Settings | None = None,
) -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            get_engine(settings),
            expire_on_commit=False,
            class_=AsyncSession,
        )
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a short-lived AsyncSession."""
    sm = get_sessionmaker()
    async with sm() as session:
        yield session


async def close_engine() -> None:
    """Dispose the engine and reset module state (safe to call multiple times)."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


def reset_engine_for_tests() -> None:
    """Synchronous reset helper for tests that swap settings between cases."""
    global _engine, _sessionmaker
    _engine = None
    _sessionmaker = None
