# apiV2

Read-only, search-heavy REST API powering the usKoreaJob web app.

FastAPI 0.115 · Python 3.13 · SQLAlchemy 2 async + asyncpg · Meilisearch v1.10 · Redis 7 · uv for deps/env · structlog JSON logs · Alembic (no-op baseline, schema owned by ETL).

---

## Table of contents

1. [Architecture](#architecture)
2. [Local setup (`uv`)](#local-setup-uv)
3. [Local stack (`docker compose`)](#local-stack-docker-compose)
4. [Environment variables](#environment-variables)
5. [Endpoint reference](#endpoint-reference)
6. [Sync worker runbook](#sync-worker-runbook)
7. [Coolify deployment](#coolify-deployment)
8. [Troubleshooting](#troubleshooting)
9. [Testing](#testing)

---

## Architecture

```mermaid
flowchart LR
    Client[Web App] -->|HTTPS + CORS| API[FastAPI apiV2]
    API -->|search · facets · suggest| Meili[(Meilisearch)]
    API -->|detail · stats · /ready| PG[(Postgres job_postings)]
    API -->|cache · rate-limit| Redis[(Redis 7)]
    Sync[Sync Worker CLI cron] -->|read updated rows| PG
    Sync -->|batched upsert| Meili
    Sync -->|watermark · lock| Redis
```

Two services share one repo:

- **apiV2** — `uvicorn app.main:app` serving `/api/v1/jobs[...]`, `/health`, `/ready`.
- **apiV2-sync** — `python -m app.sync.cli ...`, run on cron (15 min incremental + nightly full).

Postgres schema is owned by the external ETL pipeline (`/home/dwk/code/usKoreaJob/etl`). apiV2 never issues DDL against `job_postings`; the Alembic baseline is a no-op so future V2-owned tables can extend the lineage cleanly.

---

## Local setup (`uv`)

Python 3.13 and [uv](https://docs.astral.sh/uv/) are required.

```bash
# 1. Install deps into a local .venv
uv sync

# 2. Copy env template and fill in values
cp .env.example .env

# 3. Bring up Postgres + Redis + Meilisearch (or point .env at live ones)
docker compose -f docker-compose.dev.yml up -d postgres redis meilisearch

# 4. Stamp Alembic once (ETL schema already exists)
uv run python scripts/stamp_baseline.py

# 5. Seed Meilisearch from the Postgres data
uv run python -m app.sync.cli init-index
uv run python -m app.sync.cli reindex full

# 6. Run the API locally
uv run fastapi dev app/main.py
```

Linters + tests:

```bash
uv run ruff check .            # lint
uv run pytest -q               # full suite (unit + integration)
uv run pytest -q -k "not integration"   # unit only (no Docker required)
```

---

## Local stack (`docker compose`)

The provided `docker-compose.dev.yml` spins up the whole stack and does a one-shot reindex against 25 seed rows:

```bash
docker compose -f docker-compose.dev.yml up --build
```

What happens on first start:

1. Postgres 17 initialises with `tests/fixtures/seed.sql` — creates `job_postings`, the ETL index set, and loads 25 rows covering every source, language, and salary shape.
2. Redis 7 and Meilisearch v1.10 start and become healthy.
3. The `api` container runs Alembic (`ALEMBIC_MODE=stamp` in dev) and starts uvicorn on port 8000.
4. The `sync` one-shot runs `init-index` then `reindex full`, exits 0.

Host ports (non-standard to avoid colliding with locally running services):

| Service        | Container port | Host port |
| -------------- | -------------- | --------- |
| Postgres       | 5432           | 55432     |
| Redis          | 6379           | 56379     |
| Meilisearch    | 7700           | 57700     |
| API            | 8000           | 58000     |

Quick smoke tests once the stack is healthy:

```bash
curl -s http://localhost:58000/health
curl -s http://localhost:58000/ready
curl -s 'http://localhost:58000/api/v1/jobs?q=pharmacy&limit=5' | jq
curl -s 'http://localhost:58000/api/v1/jobs?q=현대&limit=5' | jq
curl -s  http://localhost:58000/api/v1/jobs/stats | jq
```

Tear down (volumes included):

```bash
docker compose -f docker-compose.dev.yml down -v
```

---

## Environment variables

All configuration is loaded by `pydantic-settings` from the environment, with `.env` taken as a fallback. See `.env.example` for a full template.

| Variable                         | Default                                         | Purpose |
| -------------------------------- | ----------------------------------------------- | ------- |
| `APP_NAME`                       | `apiV2`                                         | Reported in `/docs`, logs |
| `DEBUG`                          | `false`                                         | FastAPI debug flag |
| `LOG_LEVEL`                      | `INFO`                                          | structlog + uvicorn |
| `DATABASE_URL`                   | `postgresql+asyncpg://dev:devpassword@localhost:5432/job` | Async SQLAlchemy DSN |
| `REDIS_URL`                      | `redis://localhost:6379/0`                      | Cache + rate-limit |
| `REDIS_MAX_CONNECTIONS`          | `10`                                            | Pool size |
| `REDIS_SOCKET_TIMEOUT`           | `5`                                             | Seconds |
| `REDIS_SOCKET_CONNECT_TIMEOUT`   | `5`                                             | Seconds |
| `MEILI_URL`                      | `http://localhost:7700`                         | Meilisearch endpoint |
| `MEILI_MASTER_KEY`               | —                                               | Admin key for writes |
| `MEILI_INDEX_NAME`               | `jobs`                                          | Index name |
| `MEILI_TIMEOUT_MS`               | `5000`                                          | Client-side timeout |
| `CORS_ORIGINS`                   | —                                               | Comma-separated; `*` allows all |
| `RATE_LIMIT_ENABLED`             | `true`                                          | Master toggle |
| `RATE_LIMIT_LIST_PER_MIN`        | `120`                                           | `/jobs`, `/jobs/facets` |
| `RATE_LIMIT_SUGGEST_PER_MIN`     | `30`                                            | `/jobs/suggest` |
| `RATE_LIMIT_DEFAULT_PER_MIN`     | `60`                                            | `/jobs/{id}`, `/stats`, etc. |
| `CACHE_TTL_LIST`                 | `60`                                            | Seconds |
| `CACHE_TTL_FACETS`               | `120`                                           | Seconds |
| `CACHE_TTL_STATS`                | `300`                                           | Seconds |
| `CACHE_TTL_SUGGEST`              | `30`                                            | Seconds |
| `CACHE_TTL_DETAIL`               | `300`                                           | Seconds |
| `SYNC_BATCH_SIZE`                | `500`                                           | Rows per Meili batch |
| `SYNC_LOCK_TTL`                  | `3600`                                          | Seconds — prevents overlapping syncs |
| `SYNC_DESCRIPTION_MAX_BYTES`     | `4096`                                          | Truncate description in Meili docs |
| `ALEMBIC_MODE`                   | `upgrade` (via `entrypoint.sh`)                 | `upgrade` (default), `stamp`, or `skip` |

---

## Endpoint reference

All endpoints emit the same envelope on non-2xx responses:

```json
{"error": {"code": "NOT_FOUND",
           "message": "Job not found",
           "detail": {"id": 42}}}
```

Rate-limited responses (HTTP 429) include `Retry-After` and `X-RateLimit-*` headers. Every response carries `X-Request-ID` (echoed from the request if valid, otherwise freshly generated).

| Method | Path                              | Source           | Notes                                    |
| ------ | --------------------------------- | ---------------- | ---------------------------------------- |
| GET    | `/health`                         | —                | Liveness — skip-listed from rate limits  |
| GET    | `/ready`                          | PG + Redis + Meili | Parallel probe; 200 on all-ok else 503 |
| GET    | `/api/v1/jobs`                    | Meili            | List/search + filters + facets + cursor  |
| GET    | `/api/v1/jobs/suggest`            | Meili            | Title/company autocomplete               |
| GET    | `/api/v1/jobs/facets`             | Meili            | Filter-aware facet counts                |
| GET    | `/api/v1/jobs/stats`              | Postgres         | Aggregates                               |
| GET    | `/api/v1/jobs/{id:int}`           | Postgres         | Detail by numeric id                     |
| GET    | `/api/v1/jobs/record/{record_id}` | Postgres         | Detail by scraper-stable `record_id`     |

### `GET /api/v1/jobs`

Query parameters: `q`, `source[]`, `language`, `job_category[]`, `location_state`, `location_city`, `salary_min`, `salary_max`, `salary_unit`, `salary_currency`, `post_date_from`, `post_date_to`, `company_inferred`, `sort` (`relevance` | `newest` | `salary_high` | `salary_low` | `company_az`), `cursor`, `limit` (1–100, default 20).

Examples (assumes the compose stack on port 58000):

```bash
# English full-text search with filter + facets
curl -s 'http://localhost:58000/api/v1/jobs?q=pharmacy&source=indeed&limit=5' | jq

# Korean prefix search
curl -s 'http://localhost:58000/api/v1/jobs?q=현대&limit=5' | jq

# Newest Korean-language jobs in California
curl -s 'http://localhost:58000/api/v1/jobs?language=korean&location_state=CA&sort=newest&limit=10' | jq

# Highest-paying parsed jobs
curl -s 'http://localhost:58000/api/v1/jobs?salary_unit=yearly&sort=salary_high&limit=5' | jq

# Follow-up page using the previous response's next_cursor
curl -s 'http://localhost:58000/api/v1/jobs?limit=5&cursor=<CURSOR>' | jq
```

Response shape:

```json
{
  "items": [ /* JobSummary[] */ ],
  "facets": {
    "source":         {"gtksa": 3, "indeed": 3, "linkedin": 3, "...": 0},
    "language":       {"korean": 11, "english": 10, "bilingual": 4},
    "job_category":   {"office": 8, "warehouse": 4, "...": 0},
    "location_state": {"CA": 6, "GA": 3, "...": 0},
    "salary_bucket":  {"free": 5, "under_40k": 7, "40k_80k": 8, "80k_120k": 4, "over_120k": 1}
  },
  "next_cursor": "eyJtb2RlIjoia3MiLCAic29ydCI6IC4uLn0=",
  "total_estimated": 25
}
```

### `GET /api/v1/jobs/suggest`

```bash
curl -s 'http://localhost:58000/api/v1/jobs/suggest?q=pharm&limit=5' | jq
curl -s 'http://localhost:58000/api/v1/jobs/suggest?q=현대' | jq
```

### `GET /api/v1/jobs/facets`

Same filter params as `/jobs` but returns only `{facets, total_estimated}`.

### `GET /api/v1/jobs/stats`

```bash
curl -s http://localhost:58000/api/v1/jobs/stats | jq
```

```json
{
  "total_jobs": 25,
  "by_source":   {"gtksa": 3, "indeed": 3, "...": 0},
  "by_language": {"korean": 11, "...": 0},
  "by_category": {"office": 8, "...": 0},
  "salary_stats": {
    "min_salary": 40000.0,
    "max_salary": 120000.0,
    "avg_salary": 65294.11,
    "sample_size": 17
  }
}
```

### Detail lookups

```bash
curl -s http://localhost:58000/api/v1/jobs/1 | jq
curl -s http://localhost:58000/api/v1/jobs/record/seed-001 | jq
```

---

## Sync worker runbook

The sync worker shares the same Docker image as the API — just the command differs.

### Commands

```bash
# Idempotent: creates the Meili index if missing and applies settings.
python -m app.sync.cli init-index

# Full rebuild from Postgres. Wipes and re-pushes every row.
python -m app.sync.cli reindex full

# Delta sync — pushes rows whose `updated_at` exceeds the stored watermark.
# Fast, runs every 15 minutes in production.
python -m app.sync.cli reindex incremental
```

### Cadence (production)

- Every 15 min → `reindex incremental`
- Nightly 03:00 → `reindex full`

Both advisory-lock the same Redis key (`sync:jobs:lock`, TTL 3600 s) so they never overlap.

### Watermark reset

If the watermark gets out of step (e.g. after a DB restore), drop the Redis key and run a full reindex:

```bash
redis-cli DEL sync:jobs:watermark
python -m app.sync.cli reindex full
```

### Re-stamp Alembic

If a new environment is provisioned with an ETL-created schema:

```bash
uv run python scripts/stamp_baseline.py
```

This writes the baseline revision into `alembic_version` without issuing any DDL.

---

## Coolify deployment

Deploy the whole stack as a single **Docker Compose** application in Coolify. The compose file at the repo root (`docker-compose.yml`) defines three services from a single image:

| Service     | Role                                                                                   | Alembic          | Restart           |
| ----------- | -------------------------------------------------------------------------------------- | ---------------- | ----------------- |
| `api`       | FastAPI behind Coolify's Traefik (auto-TLS via `SERVICE_FQDN_API_8000`).               | `upgrade` (runs) | `unless-stopped`  |
| `sync-init` | One-shot per deploy: `init-index` + `reindex incremental` (seeds on fresh install).    | `skip`           | `no`              |
| `sync-cron` | Long-running **supercronic**: `*/15` incremental + `0 3 * * *` full, reads `deploy/crontab`. | `skip`       | `unless-stopped`  |

Overlap is safe because `app/sync/runner.py` advisory-locks the Redis key `sync:jobs:lock` (TTL = `SYNC_LOCK_TTL`, default 1 h).

### Prerequisites

1. Coolify v4 running on the target host (network `coolify` present — verify with `docker network ls`).
2. Managed **Postgres**, **Redis**, and **Meilisearch** resources already created in Coolify on the same project. For each, open Resource → **Connect** → copy the *internal* connection string / hostname.

### Deploy steps

1. **Create the application.** Coolify UI → `+ New` → **Application** → *Docker Compose*. Point at this Git repo, branch as appropriate, compose file path `docker-compose.yml`. Build pack: *dockerfile*, context `.`.
2. **Paste env vars.** Open the new app's **Environment** tab. Copy every key from [`deploy/.env.production.example`](deploy/.env.production.example) and fill in real values:
   - `DATABASE_URL=postgresql+asyncpg://<user>:<password>@<pg-internal-host>:5432/<db>`
   - `REDIS_URL=redis://<redis-internal-host>:6379/0` (add `:<password>@` before the host if Redis requires auth)
   - `MEILI_URL=http://<meili-internal-host>:7700`
   - `MEILI_MASTER_KEY=<secret>`
   - `CORS_ORIGINS=https://your-frontend-domain`
   - `SERVICE_FQDN_API_8000=<your-api-domain>` (leave blank to get a Coolify-generated `*.sslip.io`)
   - `COOLIFY_NETWORK=coolify` (override only if `docker network ls` shows a different shared network name)
3. **Deploy.** Coolify builds the image, spins up `api` (migrations run), waits for `/health`, runs `sync-init`, then starts `sync-cron`. Expect the first deploy to take 2–5 min.
4. **Smoke test.**
   ```bash
   curl -s https://<SERVICE_FQDN_API_8000>/health
   curl -s https://<SERVICE_FQDN_API_8000>/ready | jq
   curl -s 'https://<SERVICE_FQDN_API_8000>/api/v1/jobs?limit=3' | jq
   ```
5. **Verify the cron.** In Coolify → Logs, select `sync-cron`. You should see supercronic load the crontab on boot. After the first scheduled tick (≤15 min) you'll see a `sync.incremental.done` structlog line.

### Forcing a full reindex

From the Coolify UI → `sync-cron` service → **Execute command**:

```bash
python -m app.sync.cli reindex full
```

or drop the Redis watermark first to force a full delta on the next incremental tick:

```bash
redis-cli -u "$REDIS_URL" DEL sync:jobs:watermark
```

### One-time Alembic stamp (only needed when provisioning a brand-new Postgres whose schema was populated by the ETL out-of-band)

Run once from a machine with credentials:

```bash
uv run python scripts/stamp_baseline.py
```

The `api` service's `ALEMBIC_MODE=upgrade` is a no-op against the baseline, so subsequent deploys stay clean.

### Updating

Push to the tracked branch → Coolify auto-rebuilds and rolls `api` with zero-downtime container swap. `sync-init` re-runs on every deploy (idempotent); `sync-cron` is replaced in place.

---

## Troubleshooting

### `/ready` returns 503

Check `components`:

```bash
curl -s http://localhost:58000/ready | jq
```

- `"postgres": "unhealthy"` — verify `DATABASE_URL`, container network, PG not in recovery mode.
- `"redis": "unhealthy"` — verify `REDIS_URL`. Note: the API still serves all endpoints when Redis is down (cache + rate-limit fail open); only `/ready` flips to 503.
- `"meilisearch": "unhealthy"` — verify `MEILI_URL`, `MEILI_MASTER_KEY`. Search endpoints will return 503 until this is green again.

### Meilisearch out of sync with Postgres

Symptoms: `/api/v1/jobs/{id}` (PG) disagrees with `/api/v1/jobs` (Meili) for a specific record.

```bash
# Force a full rebuild — overwrites every document.
python -m app.sync.cli reindex full
```

If the index is obviously broken (wrong settings, wrong field types):

```bash
# From a one-off shell in the sync container:
python -c "
import asyncio
from app.search.meili import get_meili_client
from app.config import get_settings

async def go():
    s = get_settings()
    c = get_meili_client(s)
    await c.delete_index_if_exists(s.meili_index_name)

asyncio.run(go())
"
python -m app.sync.cli init-index
python -m app.sync.cli reindex full
```

### Rate-limit 429s in dev

The dev stack already loosens caps (600/300/300/min) but you can also disable entirely:

```bash
RATE_LIMIT_ENABLED=false docker compose -f docker-compose.dev.yml up
```

Or set `X-Forwarded-For` to a fresh IP per request if you only want to bypass a single client's bucket.

### Cache stampede on popular queries

The default read-through is single-flight per process, not globally coordinated. If thundering-herd becomes an issue:

- Increase `CACHE_TTL_LIST`/`CACHE_TTL_FACETS` modestly.
- Consider a Redis-side SETNX-based lock around the loader (not yet implemented — track as future work).

### Logs

Logs are structured JSON on stdout. Every line carries `request_id`, `path`, `method`, `status`, `latency_ms` (for request logs) plus whatever keys the emitting code binds.

```bash
docker compose -f docker-compose.dev.yml logs -f api | jq -c
```

---

## Testing

```bash
uv run ruff check .
uv run pytest -q
uv run pytest -q -k "not integration"    # skip Docker-dependent tests
uv run pytest -q tests/unit              # fast feedback loop
```

Integration tests require Docker (they drive Postgres, Redis, and Meilisearch through testcontainers). They're marked with `@pytest.mark.integration` so they can be deselected in environments without Docker.

---

Questions / issues → open a ticket in the usKoreaJob repo.
