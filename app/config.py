"""Application configuration loaded from environment / .env via pydantic-settings."""
from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """All environment-driven runtime configuration.

    Values are resolved from (highest precedence first):
      1. actual environment variables
      2. the `.env` file in the working directory
      3. the defaults declared here
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ─────── App ───────
    app_name: str = "apiV2"
    debug: bool = False
    log_level: str = "INFO"

    # ─────── Postgres (async SQLAlchemy DSN) ───────
    database_url: str = (
        "postgresql+asyncpg://dev:devpassword@localhost:5432/job"
    )

    # ─────── Redis ───────
    redis_url: str = "redis://localhost:6379/0"
    redis_max_connections: int = 10
    redis_socket_timeout: int = 5
    redis_socket_connect_timeout: int = 5

    # ─────── Meilisearch ───────
    meili_url: str = "http://localhost:7700"
    meili_master_key: str = ""
    meili_index_name: str = "jobs"
    meili_timeout_ms: int = 5000

    # ─────── CORS ───────
    # Comma-separated in env; parsed into list[str]. `*` allows all origins.
    # NoDecode disables pydantic-settings' JSON pre-parse so the string
    # reaches our `field_validator(mode="before")` intact.
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # ─────── Rate limiting ───────
    rate_limit_enabled: bool = True
    rate_limit_list_per_min: int = 120
    rate_limit_suggest_per_min: int = 30
    rate_limit_default_per_min: int = 60

    # ─────── Cache TTLs (seconds) ───────
    cache_ttl_list: int = 60
    cache_ttl_facets: int = 120
    cache_ttl_stats: int = 300
    cache_ttl_suggest: int = 30
    cache_ttl_detail: int = 300

    # ─────── Sync worker ───────
    sync_batch_size: int = 500
    sync_lock_ttl: int = 3600
    sync_description_max_bytes: int = 4096

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, v: object) -> object:
        """Accept a comma-separated string OR a list. Empty string → []."""
        if v is None or v == "":
            return []
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, v: str) -> str:
        return v.upper()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached accessor. Tests should override via env + `get_settings.cache_clear()`."""
    return Settings()
