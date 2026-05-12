"""Unit tests for cursor round-trip and keyset filter synthesis."""
from __future__ import annotations

import pytest

from app.exceptions import ValidationFailed
from app.search.cursor import (
    MAX_PAGE,
    build_ks_cursor,
    build_pg_cursor,
    decode_cursor,
    encode_cursor,
    is_keyset_sort,
    ks_filter_for_next_page,
)


def test_encode_decode_roundtrip() -> None:
    payload = {"mode": "ks", "sort": "newest", "last": {"post_date_ts": 123, "id": 7}}
    cur = encode_cursor(payload)
    assert isinstance(cur, str)
    assert "=" not in cur  # base64 padding stripped
    assert decode_cursor(cur) == payload


def test_decode_invalid_cursor_raises() -> None:
    with pytest.raises(ValidationFailed):
        decode_cursor("###not-base64###")


def test_is_keyset_sort() -> None:
    assert is_keyset_sort("newest")
    assert is_keyset_sort("salary_high")
    assert is_keyset_sort("salary_low")
    assert not is_keyset_sort("relevance")
    assert not is_keyset_sort("company_az")


def test_ks_filter_for_newest_desc() -> None:
    last = {"post_date_ts": 1000, "id": 42}
    expr = ks_filter_for_next_page("newest", last)
    assert expr == (
        "((post_date_ts < 1000) OR (post_date_ts = 1000 AND id < 42))"
    )


def test_ks_filter_for_salary_low_asc() -> None:
    expr = ks_filter_for_next_page("salary_low", {"salary_min": 60_000, "id": 9})
    assert expr == (
        "((salary_min > 60000) OR (salary_min = 60000 AND id > 9))"
    )


def test_ks_filter_none_for_page_mode_sorts() -> None:
    assert ks_filter_for_next_page("relevance", {"id": 1}) is None
    assert ks_filter_for_next_page("company_az", {"id": 1}) is None


def test_ks_filter_missing_fields_raises() -> None:
    with pytest.raises(ValidationFailed):
        ks_filter_for_next_page("newest", {"id": 1})  # no post_date_ts


def test_build_ks_cursor_skips_when_key_missing() -> None:
    assert build_ks_cursor("newest", {"id": 1, "post_date_ts": None}) is None


def test_build_ks_cursor_round_trip() -> None:
    hit = {"id": 7, "post_date_ts": 555, "record_id": "x"}
    cur = build_ks_cursor("newest", hit)
    assert cur is not None
    assert decode_cursor(cur) == {
        "mode": "ks", "sort": "newest", "last": {"post_date_ts": 555, "id": 7}
    }


def test_build_pg_cursor_respects_max_page() -> None:
    assert build_pg_cursor(2) is not None
    assert decode_cursor(build_pg_cursor(3)) == {"mode": "pg", "page": 3}  # type: ignore[arg-type]
    assert build_pg_cursor(MAX_PAGE + 1) is None
