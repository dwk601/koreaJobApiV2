"""Seed utilities for integration tests.

Inserts a deterministic set of rows into the test `job_postings` table via
ORM (so the assertions match the columns the API reads).
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import JobPosting

SEED_ROWS: list[dict[str, Any]] = [
    # English, parsed yearly salary, full location, retail/healthcare
    {
        "record_id": "seed-eng-1",
        "source": "indeed",
        "title": "Pharmacy Account Executive",
        "company": "986 Pharmacy",
        "company_inferred": False,
        "location": {"raw": "San Marino, CA 91108", "city": "San Marino", "state": "CA"},
        "salary": {"min": 75000.0, "max": 100000.0, "unit": "yearly",
                   "currency": "USD", "parsed": True, "raw": "$75k-$100k"},
        "description": "Sell pharmacy services.",
        "description_length": 22,
        "job_category": ["retail", "healthcare"],
        "language": "english",
        "post_date": date(2026, 5, 1),
        "scraped_at": datetime(2026, 5, 11, 12, 0, tzinfo=UTC),
        "meta": {"record_id": "seed-eng-1", "schema_version": "1.0"},
    },
    # Korean, null post_date, no salary parsing, GA
    {
        "record_id": "seed-kor-1",
        "source": "gtksa",
        "title": "앨라배마주 Ford 1차 협력사 – Production Team Manager 채용",
        "company": "아이씨엔그룹",
        "company_inferred": False,
        "location": {"raw": "AL", "city": None, "state": "AL"},
        "salary": {"min": None, "max": None, "unit": None, "currency": None,
                   "parsed": False, "raw": None},
        "description": "제조업 관리 포지션.",
        "description_length": 11,
        "job_category": ["manufacturing", "office"],
        "language": "korean",
        "post_date": None,
        "scraped_at": datetime(2026, 5, 12, 9, 30, tzinfo=UTC),
        "meta": {"record_id": "seed-kor-1", "schema_version": "1.0"},
    },
    # Bilingual, parsed yearly salary, GA
    {
        "record_id": "seed-bi-1",
        "source": "gtksa",
        "title": "현대/기아글로비스 사업장 내 근무, 물류/품질관리 신입 채용",
        "company": "아이씨엔그룹",
        "company_inferred": False,
        "location": {"raw": "West Point, GA", "city": "West Point", "state": "GA"},
        "salary": {"min": 55000.0, "max": 55000.0, "unit": "yearly",
                   "currency": "USD", "parsed": True, "raw": "$55K/year"},
        "description": "Logistics coordination role. 물류 관리.",
        "description_length": 36,
        "job_category": ["office", "warehouse", "manufacturing"],
        "language": "bilingual",
        "post_date": date(2026, 5, 12),
        "scraped_at": datetime(2026, 5, 12, 11, 0, tzinfo=UTC),
        "meta": {"record_id": "seed-bi-1", "schema_version": "1.0"},
    },
    # English hourly parsed (should NOT count toward salary_stats)
    {
        "record_id": "seed-eng-hourly",
        "source": "linkedin",
        "title": "Warehouse Associate",
        "company": "LinkedIn Warehouse Inc",
        "company_inferred": True,
        "location": {"raw": "Seattle, WA", "city": "Seattle", "state": "WA"},
        "salary": {"min": 20.0, "max": 25.0, "unit": "hourly",
                   "currency": "USD", "parsed": True, "raw": "$20-25/hr"},
        "description": "Sort packages.",
        "description_length": 14,
        "job_category": ["warehouse"],
        "language": "english",
        "post_date": date(2026, 5, 10),
        "scraped_at": datetime(2026, 5, 10, 8, 0, tzinfo=UTC),
        "meta": {"record_id": "seed-eng-hourly", "schema_version": "1.0"},
    },
    # Korean, missing company (null), category with delivery
    {
        "record_id": "seed-kor-2",
        "source": "koreadaily",
        "title": "배달 기사 구함",
        "company": None,
        "company_inferred": False,
        "location": {"raw": "Los Angeles, CA", "city": "Los Angeles", "state": "CA"},
        "salary": {"min": 3000.0, "max": 5000.0, "unit": "monthly",
                   "currency": "USD", "parsed": True, "raw": "$3-5k/mo"},
        "description": "Delivery driver.",
        "description_length": 16,
        "job_category": ["delivery"],
        "language": "korean",
        "post_date": date(2026, 5, 9),
        "scraped_at": datetime(2026, 5, 9, 15, 0, tzinfo=UTC),
        "meta": {"record_id": "seed-kor-2", "schema_version": "1.0"},
    },
]


async def seed_jobs(session: AsyncSession) -> None:
    """Insert SEED_ROWS; safe to call multiple times (record_id is unique)."""
    for row in SEED_ROWS:
        session.add(JobPosting(**row))
    await session.commit()


async def clear_jobs(session: AsyncSession) -> None:
    from sqlalchemy import text

    await session.execute(text("TRUNCATE TABLE job_postings RESTART IDENTITY"))
    await session.commit()
