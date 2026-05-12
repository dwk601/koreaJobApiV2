"""Declarative ORM models.

⚠️  READ-ONLY. The `job_postings` schema is owned by the ETL pipeline at
    /home/dwk/code/usKoreaJob/etl (see `etl/load/loader.py`).

    V2 must NOT DDL these tables. The Alembic baseline migration
    (`alembic/versions/0001_baseline.py`) is intentionally a no-op;
    `scripts/stamp_baseline.py` sets the version table so future V2-owned
    tables can extend the lineage without disturbing the ETL-owned schema.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Common declarative base."""


class JobPosting(Base):
    """Mirror of the live `job_postings` table.

    Column types match the ETL loader's DDL byte-for-byte so session-level
    reads round-trip without coercion surprises.
    """

    __tablename__ = "job_postings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    record_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    company: Mapped[str | None] = mapped_column(Text)
    company_inferred: Mapped[bool] = mapped_column(Boolean, default=False)
    location: Mapped[dict | None] = mapped_column(JSONB)
    salary: Mapped[dict | None] = mapped_column(JSONB)
    description: Mapped[str | None] = mapped_column(Text)
    description_length: Mapped[int | None] = mapped_column(Integer)
    job_category: Mapped[list | None] = mapped_column(JSONB)
    language: Mapped[str | None] = mapped_column(String(20))
    post_date: Mapped[date | None] = mapped_column(Date)
    post_date_raw: Mapped[str | None] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(Text)
    contact: Mapped[str | None] = mapped_column(Text)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    meta: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
