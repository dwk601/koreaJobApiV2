"""Sync worker CLI.

Subcommands:
  * ``init-index``          — create the ``jobs`` index and apply settings (Task 4)
  * ``reindex full``        — full rebuild from Postgres (Task 5)
  * ``reindex incremental`` — watermark-based delta (Task 5)

Run with::

    uv run python -m app.sync.cli init-index
    uv run python -m app.sync.cli reindex full
    uv run python -m app.sync.cli reindex incremental
"""
from __future__ import annotations

import asyncio

import typer
from meilisearch_python_sdk.errors import MeilisearchApiError

from app.cache.redis_client import close_redis, get_redis
from app.config import get_settings
from app.db.engine import close_engine, get_sessionmaker
from app.logging import configure_logging, get_logger
from app.search.index_config import build_index_settings
from app.search.meili import close_meili, get_meili_client, with_retries
from app.sync.runner import full_reindex, incremental_reindex

app = typer.Typer(add_completion=False, help="apiV2 sync worker")
reindex_app = typer.Typer(add_completion=False, help="Reindex commands")
app.add_typer(reindex_app, name="reindex")

logger = get_logger(__name__)


async def _init_index() -> None:
    settings = get_settings()
    client = get_meili_client(settings)
    try:
        # Idempotent: create the index if it doesn't yet exist.
        async def _create() -> None:
            try:
                await client.get_index(settings.meili_index_name)
            except MeilisearchApiError as exc:
                # Only "index not found" justifies creating it. Anything
                # else (auth, bad URL, master key mismatch) must surface
                # so operators can see it in the container logs rather
                # than being swallowed and then failing later in an
                # even more confusing way.
                if exc.code != "index_not_found":
                    raise
                await client.create_index(
                    settings.meili_index_name,
                    primary_key="id",
                    wait=True,
                )

        await with_retries("meili.create_index", _create)

        index = client.index(settings.meili_index_name)

        async def _update() -> None:
            task = await index.update_settings(build_index_settings())
            await client.wait_for_task(task.task_uid, timeout_in_ms=60_000)

        await with_retries("meili.update_settings", _update)

        logger.info(
            "meili.init_index.ok",
            index=settings.meili_index_name,
            url=settings.meili_url,
        )
    finally:
        await close_meili()


@app.command("init-index")
def init_index() -> None:
    """Create the Meilisearch index (if missing) and apply settings."""
    configure_logging(get_settings().log_level)
    asyncio.run(_init_index())


async def _ensure_index_and_run(op: str) -> dict[str, int]:
    """Shared prelude for reindex commands: ensure index + open clients."""
    await _init_index()  # idempotent; resets module client in finally
    sm = get_sessionmaker()
    meili = get_meili_client()
    redis = get_redis()
    try:
        async with sm() as session:
            if op == "full":
                return await full_reindex(session, meili, redis)
            return await incremental_reindex(session, meili, redis)
    finally:
        await close_meili()
        await close_redis()
        await close_engine()


@reindex_app.command("full")
def reindex_full() -> None:
    """Push every row from Postgres to Meilisearch."""
    configure_logging(get_settings().log_level)
    result = asyncio.run(_ensure_index_and_run("full"))
    typer.echo(f"full reindex: {result}")


@reindex_app.command("incremental")
def reindex_incremental() -> None:
    """Push rows whose ``updated_at`` is past the stored watermark."""
    configure_logging(get_settings().log_level)
    result = asyncio.run(_ensure_index_and_run("incremental"))
    typer.echo(f"incremental reindex: {result}")


if __name__ == "__main__":
    app()
