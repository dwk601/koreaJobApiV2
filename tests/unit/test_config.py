"""Unit tests for `app.config.Settings` (pydantic-settings)."""
from __future__ import annotations

import pytest


def _build_settings():
    """Construct a fresh Settings — bypass lru_cache; don't reload the module."""
    from app.config import Settings

    return Settings()


def test_defaults_when_env_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "APP_NAME", "DEBUG", "LOG_LEVEL", "CORS_ORIGINS",
        "RATE_LIMIT_ENABLED", "CACHE_TTL_LIST",
    ):
        monkeypatch.delenv(var, raising=False)
    # Ensure we do NOT pick up a sibling .env from the repo during unit tests.
    monkeypatch.chdir("/tmp")
    s = _build_settings()
    assert s.app_name == "apiV2"
    assert s.debug is False
    assert s.log_level == "INFO"
    assert s.cors_origins == []
    assert s.rate_limit_enabled is True
    assert s.cache_ttl_list == 60


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir("/tmp")
    monkeypatch.setenv("APP_NAME", "my-api")
    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setenv("CORS_ORIGINS", "https://a.test, https://b.test")
    monkeypatch.setenv("RATE_LIMIT_LIST_PER_MIN", "200")
    monkeypatch.setenv("CACHE_TTL_LIST", "90")

    s = _build_settings()
    assert s.app_name == "my-api"
    assert s.debug is True
    assert s.log_level == "DEBUG"
    assert s.cors_origins == ["https://a.test", "https://b.test"]
    assert s.rate_limit_list_per_min == 200
    assert s.cache_ttl_list == 90


def test_cors_origins_empty_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir("/tmp")
    monkeypatch.setenv("CORS_ORIGINS", "")
    s = _build_settings()
    assert s.cors_origins == []


def test_cors_origins_wildcard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir("/tmp")
    monkeypatch.setenv("CORS_ORIGINS", "*")
    s = _build_settings()
    assert s.cors_origins == ["*"]
