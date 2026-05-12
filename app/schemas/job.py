"""Pydantic v2 response/request schemas for job endpoints.

Detail + stats shapes are defined here (Task 3). List/search/facets/suggest
schemas are added in Tasks 6–8.
"""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class LocationOut(BaseModel):
    """Flattened view of the JSONB `location` column."""

    raw: str | None = None
    city: str | None = None
    state: str | None = None


class SalaryOut(BaseModel):
    """Flattened view of the JSONB `salary` column."""

    min: float | None = None
    max: float | None = None
    unit: str | None = None
    currency: str | None = None
    parsed: bool | None = None
    raw: str | None = None


class JobDetail(BaseModel):
    """Full detail response for a single job posting."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    record_id: str
    source: str
    title: str | None
    company: str | None
    company_inferred: bool
    location: LocationOut | None
    salary: SalaryOut | None
    description: str | None
    description_length: int | None
    job_category: list[str] | None
    language: str | None
    post_date: date | None
    post_date_raw: str | None
    link: str | None
    contact: str | None
    scraped_at: datetime
    meta: dict | None
    created_at: datetime
    updated_at: datetime


class SalaryStats(BaseModel):
    """Aggregated salary statistics (yearly parsed rows only)."""

    min_salary: float | None = None
    max_salary: float | None = None
    avg_salary: float | None = None
    sample_size: int = 0


class StatsResponse(BaseModel):
    """Aggregated counts and salary stats across the whole table."""

    total_jobs: int
    by_source: dict[str, int] = Field(default_factory=dict)
    by_language: dict[str, int] = Field(default_factory=dict)
    by_category: dict[str, int] = Field(default_factory=dict)
    salary_stats: SalaryStats = Field(default_factory=SalaryStats)


# ─────────────── Search / list DTOs (Task 6) ───────────────


class JobListQuery(BaseModel):
    """Query-string parameters for ``GET /api/v1/jobs``.

    FastAPI expands a Pydantic model into individual query params when used
    via ``Annotated[JobListQuery, Query()]``.
    """

    model_config = ConfigDict(extra="ignore")

    q: str | None = None

    source: list[str] | None = None
    language: str | None = None
    job_category: list[str] | None = None
    location_state: str | None = None
    location_city: str | None = None

    salary_min: float | None = None
    salary_max: float | None = None
    salary_unit: str | None = None
    salary_currency: str | None = None

    post_date_from: date | None = None
    post_date_to: date | None = None
    company_inferred: bool | None = None

    sort: str | None = None  # relevance|newest|salary_high|salary_low|company_az
    cursor: str | None = None
    limit: int = Field(default=20, ge=1, le=100)

    def resolved_sort(self) -> str:
        """Fall back: relevance when ``q`` is present, else ``newest``."""
        if self.sort:
            return self.sort
        return "relevance" if (self.q and self.q.strip()) else "newest"


class JobSummary(BaseModel):
    """Lean row for list/search responses."""

    id: int
    record_id: str
    title: str | None = None
    company: str | None = None
    company_inferred: bool = False
    location_city: str | None = None
    location_state: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_unit: str | None = None
    salary_currency: str | None = None
    language: str | None = None
    post_date: date | None = None
    source: str


class Facets(BaseModel):
    """Counts for the current filter context."""

    source: dict[str, int] = Field(default_factory=dict)
    language: dict[str, int] = Field(default_factory=dict)
    job_category: dict[str, int] = Field(default_factory=dict)
    location_state: dict[str, int] = Field(default_factory=dict)
    salary_bucket: dict[str, int] = Field(default_factory=dict)


class FacetsResponse(BaseModel):
    """Response for ``GET /api/v1/jobs/facets``.

    Identical fields to :class:`Facets` plus a total count of hits that
    satisfied the filter context (useful as a denominator in the UI).
    """

    facets: Facets
    total_estimated: int = 0


class JobFacetsQuery(BaseModel):
    """Query params for ``GET /api/v1/jobs/facets``.

    Same filter fields as :class:`JobListQuery` minus pagination/sort/q — this
    endpoint only produces counts.
    """

    model_config = ConfigDict(extra="ignore")

    source: list[str] | None = None
    language: str | None = None
    job_category: list[str] | None = None
    location_state: str | None = None
    location_city: str | None = None

    salary_min: float | None = None
    salary_max: float | None = None
    salary_unit: str | None = None
    salary_currency: str | None = None

    post_date_from: date | None = None
    post_date_to: date | None = None
    company_inferred: bool | None = None


class JobListResponse(BaseModel):
    """Response for ``GET /api/v1/jobs``."""

    items: list[JobSummary]
    facets: Facets
    next_cursor: str | None = None
    total_estimated: int = 0


# ─────────────── Suggest (Task 7) ───────────────


class Suggestion(BaseModel):
    value: str
    type: str  # "title" | "company"


class SuggestResponse(BaseModel):
    items: list[Suggestion] = Field(default_factory=list)
