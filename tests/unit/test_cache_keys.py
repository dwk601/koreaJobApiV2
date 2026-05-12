"""Unit tests for `app.cache.keys.make_key`."""
from __future__ import annotations

from datetime import date

from app.cache.keys import make_key


def test_prefix_is_preserved() -> None:
    assert make_key("list", {"q": "x"}).startswith("list:")


def test_same_params_different_order_produce_same_key() -> None:
    k1 = make_key("list", {"a": 1, "b": 2})
    k2 = make_key("list", {"b": 2, "a": 1})
    assert k1 == k2


def test_none_values_are_dropped() -> None:
    k_with_none = make_key("list", {"a": 1, "b": None})
    k_without_b = make_key("list", {"a": 1})
    assert k_with_none == k_without_b


def test_empty_list_dropped_but_list_with_value_kept() -> None:
    empty = make_key("list", {"src": []})
    full = make_key("list", {"src": ["a"]})
    bare = make_key("list", {})
    assert empty == bare
    assert full != bare


def test_different_values_produce_different_keys() -> None:
    assert make_key("list", {"q": "a"}) != make_key("list", {"q": "b"})


def test_different_prefixes_produce_different_keys() -> None:
    assert make_key("list", {"q": "a"}) != make_key("facets", {"q": "a"})


def test_date_values_coerced_stably() -> None:
    k1 = make_key("list", {"post_date_from": date(2026, 5, 1)})
    k2 = make_key("list", {"post_date_from": date(2026, 5, 1)})
    assert k1 == k2
