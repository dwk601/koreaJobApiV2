"""Postgres read-only repository for `job_postings`.

This module is the single place that knows SQL for:
  * single-row lookups (by numeric id or record_id), and
  * whole-table aggregates powering /stats.

Search/filter/facet queries do NOT go through Postgres — they live in
`app.search.*` and run against Meilisearch.
"""
from __future__ import annotations

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import JobPosting
from app.schemas.job import SalaryStats, StatsResponse


async def get_by_id(session: AsyncSession, job_id: int) -> JobPosting | None:
    result = await session.execute(select(JobPosting).where(JobPosting.id == job_id))
    return result.scalar_one_or_none()


async def get_by_record_id(session: AsyncSession, record_id: str) -> JobPosting | None:
    result = await session.execute(
        select(JobPosting).where(JobPosting.record_id == record_id)
    )
    return result.scalar_one_or_none()


async def stats(session: AsyncSession) -> StatsResponse:
    """Aggregate counts + salary statistics across the whole table."""
    # Totals
    total_result = await session.execute(select(func.count()).select_from(JobPosting))
    total_jobs = int(total_result.scalar_one() or 0)

    # Group-bys
    by_source = await _group_count(session, JobPosting.source)
    by_language = await _group_count(session, JobPosting.language)

    # job_category is a JSONB array → unnest to text rows before counting.
    cat_rows = await session.execute(
        text(
            """
            SELECT category, COUNT(*) AS cnt
            FROM (
                SELECT jsonb_array_elements_text(job_category) AS category
                FROM job_postings
                WHERE job_category IS NOT NULL
            ) AS s
            GROUP BY category
            ORDER BY cnt DESC
            """
        )
    )
    by_category: dict[str, int] = {row.category: int(row.cnt) for row in cat_rows}

    # Salary aggregates — only parsed yearly rows are comparable.
    salary_row = await session.execute(
        text(
            """
            SELECT
                MIN((salary->>'min')::numeric) AS min_salary,
                MAX((salary->>'max')::numeric) AS max_salary,
                AVG(((salary->>'min')::numeric + (salary->>'max')::numeric) / 2.0)
                    AS avg_salary,
                COUNT(*) AS sample_size
            FROM job_postings
            WHERE salary->>'unit' = 'yearly'
              AND (salary->>'parsed')::bool IS TRUE
              AND (salary->>'min') IS NOT NULL
              AND (salary->>'max') IS NOT NULL
            """
        )
    )
    r = salary_row.one()
    salary_stats = SalaryStats(
        min_salary=float(r.min_salary) if r.min_salary is not None else None,
        max_salary=float(r.max_salary) if r.max_salary is not None else None,
        avg_salary=float(r.avg_salary) if r.avg_salary is not None else None,
        sample_size=int(r.sample_size or 0),
    )

    return StatsResponse(
        total_jobs=total_jobs,
        by_source=by_source,
        by_language=by_language,
        by_category=by_category,
        salary_stats=salary_stats,
    )


async def _group_count(session: AsyncSession, column) -> dict[str, int]:
    """Generic COUNT(*) GROUP BY <column>, skipping NULLs."""
    stmt = (
        select(column, func.count())
        .where(column.isnot(None))
        .group_by(column)
        .order_by(func.count().desc())
    )
    rows = await session.execute(stmt)
    return {str(key): int(count) for key, count in rows.all()}
