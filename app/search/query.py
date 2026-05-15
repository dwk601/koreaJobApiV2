"""Meilisearch query builder.

Translates a validated :class:`app.schemas.job.JobListQuery` into the
concrete arguments Meilisearch expects.

Notes on filter semantics
-------------------------
* ``IN [...]`` against an array attribute (``job_category``) matches
  documents whose array contains **any** of the listed values — this is
  the "any of" semantic we want for a multi-select filter.
* Meili string comparison operators (``<``, ``>``) work only on numeric
  / date attributes; strings only support equality / ``IN`` / ``EXISTS``.
  Consequently keyset pagination is only supported for numeric sort keys
  (see ``app.search.cursor``). ``company_az`` + ``relevance`` fall back
  to page-mode.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

from app.schemas.job import JobFacetsQuery, JobListQuery

# Attributes for which Meili returns a facetDistribution on list responses.
FACET_ATTRS: list[str] = ["source", "language", "job_category", "location_state"]

# (lower_inclusive, upper_exclusive) in USD yearly. None ends open.
SALARY_BUCKETS: dict[str, tuple[float | None, float | None]] = {
    "free":        (None, None),   # rows that have no parsed salary_max
    "under_40k":   (0.0, 40000.0),
    "40k_80k":     (40000.0, 80000.0),
    "80k_120k":    (80000.0, 120000.0),
    "over_120k":   (120000.0, None),
}

# Sort expressions keyed by the public ``sort`` value.
#
# ``newest`` sorts by ``freshness_ts`` (= ``post_date_ts`` when present, else
# ``scraped_at``) so listings without a parsed ``post_date`` still appear in
# recency-ordered results instead of sinking to the epoch-0 floor.
SORT_EXPRESSIONS: dict[str, list[str]] = {
    "relevance":   [],
    "newest":      ["freshness_ts:desc", "id:desc"],
    "salary_high": ["salary_max:desc", "id:desc"],
    "salary_low":  ["salary_min:asc", "id:asc"],
    "company_az":  ["company:asc", "id:asc"],
}


def _fmt_str(v: str) -> str:
    """Quote a string for Meili filter syntax."""
    escaped = v.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _date_to_ts(d: date) -> int:
    return int(datetime.combine(d, datetime.min.time(), tzinfo=UTC).timestamp())


def build_filters(q: JobListQuery | JobFacetsQuery) -> list[str]:
    """Return a list of Meilisearch filter expressions (AND-joined downstream)."""
    parts: list[str] = []

    if q.source:
        vals = ", ".join(_fmt_str(s) for s in q.source)
        parts.append(f"source IN [{vals}]")
    if q.language:
        parts.append(f"language = {_fmt_str(q.language)}")
    if q.job_category:
        vals = ", ".join(_fmt_str(c) for c in q.job_category)
        parts.append(f"job_category IN [{vals}]")
    if q.location_state:
        parts.append(f"location_state = {_fmt_str(q.location_state)}")
    if q.location_city:
        parts.append(f"location_city = {_fmt_str(q.location_city)}")
    if q.salary_min is not None:
        parts.append(f"salary_min >= {q.salary_min}")
    if q.salary_max is not None:
        parts.append(f"salary_max <= {q.salary_max}")
    if q.salary_unit:
        parts.append(f"salary_unit = {_fmt_str(q.salary_unit)}")
    if q.salary_currency:
        parts.append(f"salary_currency = {_fmt_str(q.salary_currency)}")
    if q.post_date_from:
        # Filter on freshness (post_date when present, else scraped_at) so
        # undated listings still participate in the recency window.
        parts.append(f"freshness_ts >= {_date_to_ts(q.post_date_from)}")
    if q.post_date_to:
        parts.append(f"freshness_ts <= {_date_to_ts(q.post_date_to)}")
    if q.company_inferred is not None:
        parts.append(f"company_inferred = {'true' if q.company_inferred else 'false'}")

    return parts


def build_sort(sort: str) -> list[str]:
    return list(SORT_EXPRESSIONS.get(sort, []))


def join_filters(parts: list[str]) -> str | None:
    """AND-join filter parts; return None when empty (Meili expects None, not '')."""
    if not parts:
        return None
    return " AND ".join(f"({p})" for p in parts)


def build_salary_bucket_filter(bucket: str) -> str:
    """Return the filter expression for a named salary bucket."""
    if bucket == "free":
        # Rows without a parsed max salary — include both missing attribute
        # and explicit nulls.
        return "salary_max IS NULL OR salary_max NOT EXISTS"
    lo, hi = SALARY_BUCKETS[bucket]
    clauses: list[str] = []
    if lo is not None:
        # Inclusive on the lower edge only for "under_40k"/others (it's a
        # half-open [lo, hi) partition; use >= on lo so buckets are disjoint
        # for integer-valued boundaries).
        clauses.append(f"salary_max >= {lo}")
    if hi is not None:
        clauses.append(f"salary_max < {hi}")
    return " AND ".join(clauses) if clauses else ""
