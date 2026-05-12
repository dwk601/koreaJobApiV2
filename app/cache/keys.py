"""Cache key hashing.

Keys = ``f"{prefix}:{sha256_hex}"`` where the hex is computed from a
canonical JSON representation of the parameters (sorted keys, compact,
with ``None`` values dropped). This makes cache lookups deterministic
regardless of query-string order or missing-vs-null distinctions at the
callsite.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def _canonicalize(params: dict[str, Any]) -> dict[str, Any]:
    """Drop None/empty-list values so they don't influence the hash."""
    clean: dict[str, Any] = {}
    for k, v in params.items():
        if v is None:
            continue
        # Preserve empty string and 0 but drop None and empty lists.
        if isinstance(v, (list, tuple)) and len(v) == 0:
            continue
        clean[k] = v
    return clean


def make_key(prefix: str, params: dict[str, Any] | None = None) -> str:
    """Return a stable cache key for the given prefix + params.

    The input dict is normalised (None / empty-list values stripped, keys
    sorted, date/datetime/Decimal rendered as strings) and hashed with
    SHA-256.
    """
    canonical = _canonicalize(params or {})
    serialised = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    digest = hashlib.sha256(serialised.encode("utf-8")).hexdigest()
    return f"{prefix}:{digest}"
