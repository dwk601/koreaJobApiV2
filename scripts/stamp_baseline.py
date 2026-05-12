#!/usr/bin/env python3
"""One-shot: stamp the Alembic version table on the live DB.

Run ONCE per environment against a schema that was created out-of-band
by the ETL pipeline. Subsequent ``alembic upgrade head`` runs will then
be no-ops (baseline is empty) but the version table will be consistent,
allowing future V2-owned migrations to apply cleanly.

Usage (from the apiV2 project root)::

    uv run python scripts/stamp_baseline.py

Respects the same ``DATABASE_URL`` (via `.env` or real env vars) as the API.
"""
from __future__ import annotations

import sys
from pathlib import Path

from alembic.config import Config

from alembic import command

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    # env.py pulls the DSN from Settings; no override needed.
    command.stamp(cfg, "head")
    print("Alembic version stamped to head.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
