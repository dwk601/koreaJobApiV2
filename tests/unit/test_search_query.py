"""Unit tests for the Meili query builder."""
from __future__ import annotations

from datetime import UTC, date, datetime

from app.schemas.job import JobListQuery
from app.search.query import (
    SALARY_BUCKETS,
    build_filters,
    build_salary_bucket_filter,
    build_sort,
    join_filters,
)


def _ts(d: date) -> int:
    return int(datetime.combine(d, datetime.min.time(), tzinfo=UTC).timestamp())


def test_build_filters_empty_when_no_params() -> None:
    assert build_filters(JobListQuery()) == []


def test_build_filters_simple_equalities_and_ranges() -> None:
    q = JobListQuery(
        source=["gtksa", "indeed"],
        language="korean",
        job_category=["office"],
        location_state="CA",
        salary_min=50000,
        salary_max=120000,
        post_date_from=date(2026, 1, 1),
        company_inferred=False,
    )
    parts = build_filters(q)
    assert 'source IN ["gtksa", "indeed"]' in parts
    assert 'language = "korean"' in parts
    assert 'job_category IN ["office"]' in parts
    assert 'location_state = "CA"' in parts
    assert "salary_min >= 50000" in parts[4] or any(
        p == "salary_min >= 50000.0" or p == "salary_min >= 50000"
        for p in parts
    )
    assert any("salary_max <= 120000" in p for p in parts)
    assert f"post_date_ts >= {_ts(date(2026, 1, 1))}" in parts
    assert "company_inferred = false" in parts


def test_build_filters_escapes_quotes() -> None:
    q = JobListQuery(location_city='"Weird"')
    parts = build_filters(q)
    assert parts == ['location_city = "\\"Weird\\""']


def test_build_sort_default_sorts() -> None:
    assert build_sort("relevance") == []
    assert build_sort("newest") == ["post_date_ts:desc", "id:desc"]
    assert build_sort("salary_high") == ["salary_max:desc", "id:desc"]
    assert build_sort("salary_low") == ["salary_min:asc", "id:asc"]
    assert build_sort("company_az") == ["company:asc", "id:asc"]
    assert build_sort("unknown") == []


def test_join_filters_none_when_empty() -> None:
    assert join_filters([]) is None


def test_join_filters_and_joined_with_parens() -> None:
    joined = join_filters(['source = "a"', "salary_min >= 0"])
    assert joined == '(source = "a") AND (salary_min >= 0)'


def test_salary_bucket_filter_shapes() -> None:
    assert build_salary_bucket_filter("free") == (
        "salary_max IS NULL OR salary_max NOT EXISTS"
    )
    assert build_salary_bucket_filter("under_40k") == (
        "salary_max >= 0.0 AND salary_max < 40000.0"
    )
    assert build_salary_bucket_filter("over_120k") == "salary_max >= 120000.0"
    assert set(SALARY_BUCKETS) == {
        "free", "under_40k", "40k_80k", "80k_120k", "over_120k"
    }


def test_job_list_query_resolved_sort() -> None:
    assert JobListQuery().resolved_sort() == "newest"
    assert JobListQuery(q="pharmacy").resolved_sort() == "relevance"
    assert JobListQuery(sort="company_az").resolved_sort() == "company_az"
    assert JobListQuery(q="x", sort="newest").resolved_sort() == "newest"
