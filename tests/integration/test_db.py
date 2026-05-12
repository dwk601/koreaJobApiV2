"""Integration: async engine + session + Alembic baseline against real PG.

Marked ``integration``; requires Docker on the host.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select, text

from app.db.models import JobPosting

pytestmark = pytest.mark.integration


async def test_session_can_select_from_job_postings(pg_engine) -> None:
    """Session DI yields a working AsyncSession that sees the ETL schema."""
    from app.db.engine import get_sessionmaker

    sm = get_sessionmaker()
    async with sm() as session:
        result = await session.execute(select(JobPosting).limit(1))
        rows = result.scalars().all()
        assert rows == []  # schema present, but table is empty


async def test_jsonb_roundtrip(pg_engine) -> None:
    """Insert a row directly, then read it back through the ORM mapping."""
    from datetime import UTC, datetime

    from app.db.engine import get_sessionmaker

    sm = get_sessionmaker()
    async with sm() as session:
        session.add(
            JobPosting(
                record_id="test-1",
                source="unit",
                title="Pharmacy Account Executive",
                company="986 Pharmacy",
                company_inferred=False,
                location={"raw": "San Marino, CA", "city": "San Marino", "state": "CA"},
                salary={"min": 75000.0, "max": 100000.0, "unit": "yearly",
                        "currency": "USD", "parsed": True, "raw": "Pay: $75k-$100k"},
                description="Sells stuff.",
                description_length=12,
                job_category=["retail", "healthcare"],
                language="english",
                scraped_at=datetime.now(UTC),
                meta={"record_id": "test-1", "schema_version": "1.0"},
            )
        )
        await session.commit()

        got = await session.execute(
            select(JobPosting).where(JobPosting.record_id == "test-1")
        )
        row = got.scalar_one()
        assert row.location == {"raw": "San Marino, CA", "city": "San Marino", "state": "CA"}
        assert row.salary["unit"] == "yearly"
        assert row.job_category == ["retail", "healthcare"]
        assert row.company == "986 Pharmacy"

        await session.execute(text("DELETE FROM job_postings WHERE record_id = 'test-1'"))
        await session.commit()


async def test_alembic_upgrade_head_is_noop(pg_engine, pg_dsn_async: str) -> None:
    """Running `alembic upgrade head` against an ETL-owned schema must succeed
    without touching the domain tables."""
    import asyncio
    from pathlib import Path

    from alembic.config import Config

    from alembic import command

    project_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(project_root / "alembic.ini"))

    # Alembic loads env.py in a subthread; run via asyncio.to_thread to stay out
    # of the running loop.
    await asyncio.to_thread(command.upgrade, cfg, "head")

    # Version table is now populated at revision 0001_baseline.
    from app.db.engine import get_engine

    async with get_engine().connect() as conn:
        result = await conn.execute(text("SELECT version_num FROM alembic_version"))
        version = result.scalar_one()
        assert version == "0001_baseline"

        # Domain table still present and untouched.
        count = await conn.execute(text("SELECT COUNT(*) FROM job_postings"))
        assert count.scalar_one() == 0
