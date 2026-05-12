"""baseline (no-op: schema is owned by ETL)

Revision ID: 0001_baseline
Revises:
Create Date: 2026-05-12 12:00:00.000000

The domain schema (``job_postings``) is created and maintained by the ETL
pipeline at /home/dwk/code/usKoreaJob/etl/load/loader.py. V2 must NOT DDL
these tables. This baseline exists so we can stamp the version table
(``alembic stamp head``) and add V2-owned tables in future revisions without
disturbing the ETL-owned schema.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Intentional no-op: simply assert connectivity.
    op.execute("SELECT 1")


def downgrade() -> None:
    # Nothing to undo — we never created anything.
    pass
