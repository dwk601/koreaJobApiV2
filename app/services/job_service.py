"""Business-logic layer for job endpoints.

Task 3: detail + stats from Postgres.
Task 6: search/list via Meilisearch.
Task 9 (later): Redis caching is layered on top of these methods.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from meilisearch_python_sdk import AsyncClient as MeiliClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.keys import make_key
from app.cache.readthrough import get_or_set
from app.config import Settings, get_settings
from app.exceptions import NotFound, ValidationFailed
from app.repositories import job_postings_pg as repo
from app.schemas.job import (
    Facets,
    FacetsResponse,
    JobDetail,
    JobFacetsQuery,
    JobListQuery,
    JobListResponse,
    JobSummary,
    StatsResponse,
    Suggestion,
    SuggestResponse,
)
from app.search.cursor import (
    build_ks_cursor,
    build_pg_cursor,
    decode_cursor,
    is_keyset_sort,
    ks_filter_for_next_page,
)
from app.search.query import (
    FACET_ATTRS,
    SALARY_BUCKETS,
    build_filters,
    build_salary_bucket_filter,
    build_sort,
    join_filters,
)


class JobService:
    def __init__(
        self,
        session: AsyncSession,
        meili: MeiliClient | None = None,
        cache: Redis | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.session = session
        self.meili = meili
        self.cache = cache
        self.settings = settings or get_settings()

    # ────────────── Detail / stats (Postgres) ──────────────

    async def get_detail_by_id(self, job_id: int) -> JobDetail:
        async def _load() -> JobDetail:
            row = await repo.get_by_id(self.session, job_id)
            if row is None:
                raise NotFound(message="Job not found", detail={"id": job_id})
            return JobDetail.model_validate(row)

        return await get_or_set(
            self.cache,
            key=f"job:id:{job_id}",
            ttl=self.settings.cache_ttl_detail,
            loader=_load,
            serialize=lambda m: m.model_dump_json(),
            deserialize=JobDetail.model_validate_json,
        )

    async def get_detail_by_record_id(self, record_id: str) -> JobDetail:
        async def _load() -> JobDetail:
            row = await repo.get_by_record_id(self.session, record_id)
            if row is None:
                raise NotFound(message="Job not found", detail={"record_id": record_id})
            return JobDetail.model_validate(row)

        return await get_or_set(
            self.cache,
            key=f"job:rid:{record_id}",
            ttl=self.settings.cache_ttl_detail,
            loader=_load,
            serialize=lambda m: m.model_dump_json(),
            deserialize=JobDetail.model_validate_json,
        )

    async def get_stats(self) -> StatsResponse:
        async def _load() -> StatsResponse:
            return await repo.stats(self.session)

        return await get_or_set(
            self.cache,
            key="stats:all",
            ttl=self.settings.cache_ttl_stats,
            loader=_load,
            serialize=lambda m: m.model_dump_json(),
            deserialize=StatsResponse.model_validate_json,
        )

    # ────────────── Facets-only (filter-aware, no hits returned) ──────────────

    async def facets(self, query: JobFacetsQuery) -> FacetsResponse:
        """Return facet counts for the current filter context.

        Runs a zero-hit Meilisearch search to pull ``facetDistribution`` for
        the attribute facets, and kicks off one filtered count per salary
        bucket in parallel.
        """
        if self.meili is None:  # pragma: no cover
            raise RuntimeError("JobService.facets requires a Meili client")

        cache_key = make_key("facets", query.model_dump(exclude_none=True))

        async def _compute() -> FacetsResponse:
            return await self._facets_uncached(query)

        return await get_or_set(
            self.cache,
            key=cache_key,
            ttl=self.settings.cache_ttl_facets,
            loader=_compute,
            serialize=lambda m: m.model_dump_json(),
            deserialize=FacetsResponse.model_validate_json,
        )

    async def _facets_uncached(self, query: JobFacetsQuery) -> FacetsResponse:
        index = self.meili.index(self.settings.meili_index_name)
        filters = build_filters(query)
        filter_expr = join_filters(filters)

        async def _bucket_count(bucket: str) -> int:
            b_filter = build_salary_bucket_filter(bucket)
            combined = (
                f"({filter_expr}) AND ({b_filter})"
                if filter_expr and b_filter
                else (filter_expr or b_filter or None)
            )
            res = await index.search("", filter=combined, limit=0)
            return int(
                getattr(res, "total_hits", None)
                or getattr(res, "estimated_total_hits", 0)
            )

        main_coro = index.search(
            "", filter=filter_expr, limit=0, facets=FACET_ATTRS
        )
        bucket_coros = [_bucket_count(b) for b in SALARY_BUCKETS]

        main_result, *bucket_totals = await asyncio.gather(main_coro, *bucket_coros)

        facet_dist = getattr(main_result, "facet_distribution", None) or {}
        facets = Facets(
            source=facet_dist.get("source", {}),
            language=facet_dist.get("language", {}),
            job_category=facet_dist.get("job_category", {}),
            location_state=facet_dist.get("location_state", {}),
            salary_bucket=dict(zip(SALARY_BUCKETS.keys(), bucket_totals, strict=True)),
        )
        total = int(
            getattr(main_result, "total_hits", None)
            or getattr(main_result, "estimated_total_hits", 0)
        )
        return FacetsResponse(facets=facets, total_estimated=total)

    # ────────────── Suggest (Meilisearch prefix search over title/company) ──────────────

    async def suggest(self, prefix: str, limit: int) -> SuggestResponse:
        """Prefix-autocomplete across title and company attributes.

        Returns deduped suggestions, preferring title matches over company
        matches when the normalized value appears as both.
        """
        if self.meili is None:  # pragma: no cover - DI provides
            raise RuntimeError("JobService.suggest requires a Meili client")

        prefix = (prefix or "").strip()
        if not prefix:
            return SuggestResponse(items=[])

        cache_key = make_key("suggest", {"q": prefix, "limit": limit})

        async def _compute() -> SuggestResponse:
            return await self._suggest_uncached(prefix, limit)

        return await get_or_set(
            self.cache,
            key=cache_key,
            ttl=self.settings.cache_ttl_suggest,
            loader=_compute,
            serialize=lambda m: m.model_dump_json(),
            deserialize=SuggestResponse.model_validate_json,
        )

    async def _suggest_uncached(self, prefix: str, limit: int) -> SuggestResponse:
        index = self.meili.index(self.settings.meili_index_name)
        # Over-fetch so we can dedup on the normalized string below.
        fetch_limit = max(limit * 3, limit)

        res = await index.search(
            prefix,
            limit=fetch_limit,
            attributes_to_search_on=["title", "company"],
            attributes_to_retrieve=["title", "company"],
        )

        seen: set[str] = set()
        out: list[Suggestion] = []
        # Prefer title entries first, then company; preserves ranked order
        # from Meili but lets titles win when the same string appears in both.
        for kind in ("title", "company"):
            for hit in res.hits:
                value = (hit.get(kind) or "").strip()
                if not value:
                    continue
                normalized = value.casefold()
                if normalized in seen:
                    continue
                seen.add(normalized)
                out.append(Suggestion(value=value, type=kind))
                if len(out) >= limit:
                    return SuggestResponse(items=out)

        return SuggestResponse(items=out)

    # ────────────── Search / list (Meilisearch) ──────────────

    async def search_jobs(self, query: JobListQuery) -> JobListResponse:
        if self.meili is None:  # pragma: no cover - DI always provides it
            raise RuntimeError("JobService.search_jobs requires a Meili client")

        cache_key = make_key("list", query.model_dump(exclude_none=True))

        async def _compute() -> JobListResponse:
            return await self._search_jobs_uncached(query)

        return await get_or_set(
            self.cache,
            key=cache_key,
            ttl=self.settings.cache_ttl_list,
            loader=_compute,
            serialize=lambda m: m.model_dump_json(),
            deserialize=JobListResponse.model_validate_json,
        )

    async def _search_jobs_uncached(self, query: JobListQuery) -> JobListResponse:
        sort = query.resolved_sort()
        filters = build_filters(query)

        # ── cursor handling ──
        page_number = 1
        decoded_mode: str | None = None
        if query.cursor:
            data = decode_cursor(query.cursor)
            decoded_mode = data.get("mode")
            if decoded_mode == "ks":
                if not is_keyset_sort(sort):
                    raise ValidationFailed(
                        "Cursor mode mismatch for sort",
                        detail={"sort": sort, "cursor_mode": "ks"},
                    )
                ks_filter = ks_filter_for_next_page(sort, data["last"])
                if ks_filter:
                    filters.append(ks_filter)
            elif decoded_mode == "pg":
                if is_keyset_sort(sort):
                    raise ValidationFailed(
                        "Cursor mode mismatch for sort",
                        detail={"sort": sort, "cursor_mode": "pg"},
                    )
                page_number = int(data.get("page", 1))
            else:
                raise ValidationFailed(
                    "Unknown cursor mode", detail={"mode": decoded_mode}
                )

        filter_expr = join_filters(filters)
        sort_expr = build_sort(sort) or None

        # ── main search + parallel salary-bucket counts ──
        index = self.meili.index(self.settings.meili_index_name)

        keyset_mode = is_keyset_sort(sort)
        search_kwargs: dict[str, Any] = {
            "sort": sort_expr,
            "filter": filter_expr,
            "facets": FACET_ATTRS,
        }
        if keyset_mode:
            search_kwargs["limit"] = query.limit
        else:
            search_kwargs["page"] = page_number
            search_kwargs["hits_per_page"] = query.limit

        async def _bucket_count(bucket: str) -> int:
            b_filter = build_salary_bucket_filter(bucket)
            combined = (
                f"({filter_expr}) AND ({b_filter})"
                if filter_expr and b_filter
                else (filter_expr or b_filter or None)
            )
            res = await index.search("", filter=combined, limit=0)
            # SDK exposes both estimated/total; prefer the exact one when present.
            return int(
                getattr(res, "total_hits", None)
                or getattr(res, "estimated_total_hits", 0)
            )

        main_coro = index.search(query.q or "", **search_kwargs)
        bucket_coros = [_bucket_count(b) for b in SALARY_BUCKETS]

        main_result, *bucket_totals = await asyncio.gather(main_coro, *bucket_coros)

        # ── items ──
        items = [_hit_to_summary(h) for h in main_result.hits]

        # ── facets ──
        facet_dist = getattr(main_result, "facet_distribution", None) or {}
        facets = Facets(
            source=facet_dist.get("source", {}),
            language=facet_dist.get("language", {}),
            job_category=facet_dist.get("job_category", {}),
            location_state=facet_dist.get("location_state", {}),
            salary_bucket=dict(zip(SALARY_BUCKETS.keys(), bucket_totals, strict=True)),
        )

        # ── next cursor ──
        next_cursor: str | None = None
        if items and len(items) == query.limit:
            if keyset_mode:
                last_hit = main_result.hits[-1]
                next_cursor = build_ks_cursor(sort, last_hit)
            else:
                next_cursor = build_pg_cursor(page_number + 1)

        # ── total ──
        total = int(
            getattr(main_result, "total_hits", None)
            or getattr(main_result, "estimated_total_hits", None)
            or len(items)
        )

        return JobListResponse(
            items=items,
            facets=facets,
            next_cursor=next_cursor,
            total_estimated=total,
        )


def _hit_to_summary(hit: dict[str, Any]) -> JobSummary:
    """Map a raw Meili hit to :class:`JobSummary`."""
    post_date = None
    ts = hit.get("post_date_ts")
    if ts:
        post_date = datetime.fromtimestamp(int(ts), tz=UTC).date()

    return JobSummary(
        id=int(hit["id"]),
        record_id=hit["record_id"],
        title=hit.get("title"),
        company=hit.get("company"),
        company_inferred=bool(hit.get("company_inferred", False)),
        location_city=hit.get("location_city"),
        location_state=hit.get("location_state"),
        salary_min=hit.get("salary_min"),
        salary_max=hit.get("salary_max"),
        salary_unit=hit.get("salary_unit"),
        salary_currency=hit.get("salary_currency"),
        language=hit.get("language"),
        post_date=post_date,
        source=hit["source"],
    )
