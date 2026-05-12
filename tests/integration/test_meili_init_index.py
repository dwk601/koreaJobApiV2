"""Integration: `init-index` CLI creates the index with the correct settings."""
from __future__ import annotations

import pytest
from meilisearch_python_sdk import AsyncClient

from app.search.index_config import FILTERABLE, SEARCHABLE, SORTABLE
from app.sync.cli import _init_index

pytestmark = pytest.mark.integration


async def test_init_index_is_idempotent_and_applies_settings(meili_env: str) -> None:
    # First run — creates + applies.
    await _init_index()
    # Second run — must not raise (idempotent).
    await _init_index()

    async with AsyncClient(url=meili_env, api_key="testMasterKey") as client:
        index = client.index("jobs_test")
        settings = await index.get_settings()

        # Normalize to string lists for comparison (Pydantic models).
        assert list(settings.searchable_attributes) == SEARCHABLE
        assert set(settings.sortable_attributes) == set(SORTABLE)
        assert set(settings.filterable_attributes) == set(FILTERABLE)
        assert settings.typo_tolerance is not None
        assert settings.typo_tolerance.enabled is True
        assert settings.stop_words == []

        # Primary key survived.
        fetched_index = await client.get_index("jobs_test")
        assert fetched_index.primary_key == "id"
