"""Opaque cursor encoding for list pagination.

Two modes:

* ``ks`` — keyset. Payload: ``{"mode":"ks", "sort": <sort>,
  "last": {<sort_key>: value, "id": <int>}}``. The translator emits an
  extra Meili filter like ``(freshness_ts < X) OR
  (freshness_ts = X AND id < Y)`` for the next page.
* ``pg`` — page. Payload: ``{"mode":"pg", "page": <int>}``. Used for
  relevance-ranked and string-sorted queries where numeric keyset cannot
  be expressed in Meili filters. Max page is capped at ``MAX_PAGE``.

Cursors are base64-url-safe JSON so callers can echo them verbatim.
"""
from __future__ import annotations

import base64
import json
from typing import Any

from app.exceptions import ValidationFailed

MAX_PAGE = 50

# Which sort strings use true keyset pagination (numeric sort keys only).
KEYSET_SORT_KEYS: dict[str, tuple[str, str]] = {
    "newest":      ("freshness_ts", "desc"),
    "salary_high": ("salary_max", "desc"),
    "salary_low":  ("salary_min", "asc"),
}


def is_keyset_sort(sort: str) -> bool:
    return sort in KEYSET_SORT_KEYS


def encode_cursor(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return (
        base64.urlsafe_b64encode(raw.encode("utf-8"))
        .rstrip(b"=")
        .decode("ascii")
    )


def decode_cursor(s: str) -> dict[str, Any]:
    try:
        pad = "=" * (-len(s) % 4)
        raw = base64.urlsafe_b64decode(s + pad).decode("utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise TypeError("cursor payload must be a JSON object")
        return data
    except Exception as exc:
        raise ValidationFailed("Invalid cursor", detail={"cursor": s}) from exc


def ks_filter_for_next_page(sort: str, last: dict[str, Any]) -> str | None:
    """Build the keyset continuation filter for ``sort``.

    Returns ``None`` when the sort does not support keyset pagination.
    """
    cfg = KEYSET_SORT_KEYS.get(sort)
    if cfg is None:
        return None

    key, direction = cfg
    op = "<" if direction == "desc" else ">"
    id_op = op

    last_key = last.get(key)
    last_id = last.get("id")
    if last_key is None or last_id is None:
        raise ValidationFailed(
            "Cursor missing required keyset fields",
            detail={"expected": [key, "id"], "got": last},
        )

    return (
        f"(({key} {op} {last_key}) OR "
        f"({key} = {last_key} AND id {id_op} {last_id}))"
    )


def build_ks_cursor(sort: str, last_hit: dict[str, Any]) -> str | None:
    """Produce the cursor representing 'after this hit' for the given sort."""
    cfg = KEYSET_SORT_KEYS.get(sort)
    if cfg is None:
        return None
    key, _ = cfg
    if last_hit.get(key) is None or last_hit.get("id") is None:
        return None
    payload = {
        "mode": "ks",
        "sort": sort,
        "last": {key: last_hit[key], "id": int(last_hit["id"])},
    }
    return encode_cursor(payload)


def build_pg_cursor(page: int) -> str | None:
    if page > MAX_PAGE:
        return None
    return encode_cursor({"mode": "pg", "page": page})
