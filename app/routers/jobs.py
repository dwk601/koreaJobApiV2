"""`/api/v1/jobs` — detail, stats, list/search endpoints.

Route order note:
    `/stats` and `/record/{record_id}` MUST be declared BEFORE the catch-all
    `/{job_id}` so they are not shadowed by the integer-coerced path. FastAPI
    dispatches on declaration order when patterns could match both.

    The list endpoint lives at the router root (``""``). Suggest/facets
    arrive in Tasks 7–8.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.dependencies import get_job_service
from app.schemas.job import (
    FacetsResponse,
    JobDetail,
    JobFacetsQuery,
    JobListQuery,
    JobListResponse,
    StatsResponse,
    SuggestResponse,
)
from app.services.job_service import JobService

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


@router.get(
    "",
    response_model=JobListResponse,
    summary="List/search job postings with filters, facets, and cursor pagination",
)
async def list_jobs(
    query: Annotated[JobListQuery, Query()],
    service: JobService = Depends(get_job_service),
) -> JobListResponse:
    return await service.search_jobs(query)


@router.get(
    "/suggest",
    response_model=SuggestResponse,
    summary="Autocomplete suggestions across title and company",
)
async def suggest(
    q: Annotated[str, Query(min_length=1, max_length=100, description="Prefix")],
    limit: Annotated[int, Query(ge=1, le=20)] = 8,
    service: JobService = Depends(get_job_service),
) -> SuggestResponse:
    return await service.suggest(q, limit)


@router.get(
    "/facets",
    response_model=FacetsResponse,
    summary="Filter-aware facet counts (no hits returned)",
)
async def facets(
    query: Annotated[JobFacetsQuery, Query()],
    service: JobService = Depends(get_job_service),
) -> FacetsResponse:
    return await service.facets(query)


@router.get(
    "/stats",
    response_model=StatsResponse,
    summary="Aggregated counts + salary stats",
)
async def get_stats(service: JobService = Depends(get_job_service)) -> StatsResponse:
    return await service.get_stats()


@router.get(
    "/record/{record_id}",
    response_model=JobDetail,
    summary="Lookup a job posting by its scraper-stable `record_id`",
)
async def get_by_record_id(
    record_id: str,
    service: JobService = Depends(get_job_service),
) -> JobDetail:
    return await service.get_detail_by_record_id(record_id)


@router.get(
    "/{job_id}",
    response_model=JobDetail,
    summary="Lookup a job posting by numeric id",
)
async def get_by_id(
    job_id: int,
    service: JobService = Depends(get_job_service),
) -> JobDetail:
    return await service.get_detail_by_id(job_id)
