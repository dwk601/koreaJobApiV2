"""Meilisearch index settings for the `jobs` index.

Centralizing here means ``init-index`` (Task 4), ``reindex`` (Task 5), and
the query builder (Task 6) share one definition of which attributes are
searchable / sortable / filterable. Keep this file as the single source of
truth — if you add a filter, you must also add the attribute here.
"""
from __future__ import annotations

from meilisearch_python_sdk.models.settings import MeilisearchSettings, TypoTolerance

# Order matters — Meilisearch weights matches higher up the list.
SEARCHABLE: list[str] = ["title", "company", "description"]

# Attributes we can sort by (epoch int for dates; numeric for salaries).
SORTABLE: list[str] = [
    "post_date_ts",
    "id",
    "salary_max",
    "salary_min",
    "company",
]

# Attributes we can filter by (and therefore request facet counts on).
FILTERABLE: list[str] = [
    "id",
    "source",
    "language",
    "job_category",
    "location_state",
    "location_city",
    "company_inferred",
    "salary_min",
    "salary_max",
    "salary_unit",
    "salary_currency",
    "post_date_ts",
]


TYPO_TOLERANCE = TypoTolerance(
    enabled=True,
    # Korean/English content has short tokens; keep defaults permissive.
    min_word_size_for_typos={"one_typo": 5, "two_typos": 9},
)


def build_index_settings() -> MeilisearchSettings:
    """Return the canonical settings payload used by ``update_settings``."""
    return MeilisearchSettings(
        searchable_attributes=SEARCHABLE,
        sortable_attributes=SORTABLE,
        filterable_attributes=FILTERABLE,
        typo_tolerance=TYPO_TOLERANCE,
        stop_words=[],  # preserve short Korean tokens
    )
