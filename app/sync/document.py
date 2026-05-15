"""Map Postgres ``JobPosting`` rows into flattened Meilisearch documents.

The Meili document shape must match the attributes declared in
``app.search.index_config`` (filterable / sortable / searchable). Flattening
JSONB at sync time keeps filter expressions simple at query time.
"""
from __future__ import annotations

from datetime import UTC, datetime, time
from typing import Any

from app.db.models import JobPosting


def _truncate_utf8(text: str, max_bytes: int) -> str:
    """Return ``text`` cut so its UTF-8 byte length ≤ ``max_bytes`` without
    splitting a multi-byte character."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    # Walk back byte-by-byte until the prefix is a valid UTF-8 boundary.
    clipped = encoded[:max_bytes]
    while clipped:
        try:
            return clipped.decode("utf-8")
        except UnicodeDecodeError:
            clipped = clipped[:-1]
    return ""


def _post_date_ts(row: JobPosting) -> int:
    """Epoch seconds at UTC midnight for ``post_date``; null → 0."""
    if row.post_date is None:
        return 0
    return int(datetime.combine(row.post_date, time.min, tzinfo=UTC).timestamp())


def _freshness_ts(row: JobPosting) -> int:
    """Epoch seconds for filtering/sorting by recency.

    Falls back to ``scraped_at`` when ``post_date`` is missing so jobs from
    sources that don't publish a posting date (e.g. Indeed, JobKoreaUSA,
    Korea Daily, Radio Korea) can still participate in the freshness window
    that drives the public list/facets endpoints. ``scraped_at`` is NOT NULL
    on the source table, so this always returns a real timestamp.
    """
    if row.post_date is not None:
        return int(datetime.combine(row.post_date, time.min, tzinfo=UTC).timestamp())
    return int(row.scraped_at.timestamp())


def _salary_field(row: JobPosting, key: str) -> Any:
    salary = row.salary or {}
    return salary.get(key)


def to_meili_doc(row: JobPosting, description_max_bytes: int) -> dict[str, Any]:
    """Produce the canonical Meili document for ``row``.

    Returns a plain ``dict`` ready for ``add_documents_in_batches``.
    """
    location = row.location or {}
    description = row.description or ""
    truncated_description = _truncate_utf8(description, description_max_bytes)

    return {
        "id": row.id,
        "record_id": row.record_id,
        "source": row.source,
        "title": row.title,
        "company": row.company,
        "company_inferred": bool(row.company_inferred),
        "description": truncated_description,
        "language": row.language,
        "post_date_ts": _post_date_ts(row),
        "freshness_ts": _freshness_ts(row),
        "location_city": location.get("city"),
        "location_state": location.get("state"),
        "salary_min": _salary_field(row, "min"),
        "salary_max": _salary_field(row, "max"),
        "salary_unit": _salary_field(row, "unit"),
        "salary_currency": _salary_field(row, "currency"),
        "job_category": list(row.job_category) if row.job_category else [],
        "link": row.link,
    }
