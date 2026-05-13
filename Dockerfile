# syntax=docker/dockerfile:1.7

# ─────────────────────────────────────────────────────────────
# Stage 1 — fetch a pinned uv binary from Astral's published image.
# ─────────────────────────────────────────────────────────────
FROM ghcr.io/astral-sh/uv:0.11.4 AS uv-bin

# ─────────────────────────────────────────────────────────────
# Stage 2 — builder. Install project dependencies into /app/.venv.
# Using BuildKit cache mounts so repeat builds are fast; --frozen
# fails the build if pyproject.toml and uv.lock disagree.
# ─────────────────────────────────────────────────────────────
FROM python:3.13-slim AS builder

# uv binary from stage 1 (copied once, no download step here).
COPY --from=uv-bin /uv /usr/local/bin/uv

# uv flags:
#   UV_COMPILE_BYTECODE=1  — precompile .pyc in the venv (faster cold start)
#   UV_LINK_MODE=copy      — avoid hardlinks across overlay layers
#   UV_NO_INSTALLER_METADATA / UV_PROJECT_ENVIRONMENT — see uv docs
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Copy only the manifests first so deps are cached independently from code.
COPY pyproject.toml uv.lock ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# ─────────────────────────────────────────────────────────────
# Stage 3 — runtime. Keep it small: only the venv + source + scripts.
# Run as a non-root user. Expose /health for HEALTHCHECK via curl.
# ─────────────────────────────────────────────────────────────
FROM python:3.13-slim AS runtime

# curl is used by HEALTHCHECK; ca-certificates for TLS upstreams;
# procps provides pgrep for sync-cron's supercronic healthcheck.
# Pin versions would require the apt-cacher; rely on the base image's
# pinned snapshot for reproducibility.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates tini procps \
 && rm -rf /var/lib/apt/lists/*

# ─────────────────────────────────────────────────────────────
# supercronic — pinned, sha1-verified, multi-arch.
# Used by the sync-cron service in docker-compose.yml. Safe on the
# api service too (it's just an extra ~10 MB static binary, never
# invoked unless you override the command). Arch is picked up from
# BuildKit's TARGETARCH automatically on `docker buildx build`.
# Latest releases: https://github.com/aptible/supercronic/releases
# ─────────────────────────────────────────────────────────────
ARG TARGETARCH
RUN set -eux; \
    case "${TARGETARCH:-amd64}" in \
      amd64) \
        SUPERCRONIC_URL=https://github.com/aptible/supercronic/releases/download/v0.2.45/supercronic-linux-amd64; \
        SUPERCRONIC_SHA1SUM=e894b193bea75a5ee644e700c59e30eedc804cf7; \
        SUPERCRONIC=supercronic-linux-amd64 ;; \
      arm64) \
        SUPERCRONIC_URL=https://github.com/aptible/supercronic/releases/download/v0.2.45/supercronic-linux-arm64; \
        SUPERCRONIC_SHA1SUM=20ce6dace414a64f0632f4092d6d3745db6085ad; \
        SUPERCRONIC=supercronic-linux-arm64 ;; \
      *) echo "Unsupported TARGETARCH=${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    curl -fsSLO "${SUPERCRONIC_URL}"; \
    echo "${SUPERCRONIC_SHA1SUM}  ${SUPERCRONIC}" | sha1sum -c -; \
    chmod +x "${SUPERCRONIC}"; \
    mv "${SUPERCRONIC}" "/usr/local/bin/${SUPERCRONIC}"; \
    ln -s "/usr/local/bin/${SUPERCRONIC}" /usr/local/bin/supercronic

# Non-root user. HOME is set so uvicorn can cache without hitting /root.
RUN groupadd --system --gid 1000 appuser \
 && useradd  --system --uid 1000 --gid 1000 \
             --home-dir /home/appuser --create-home \
             --shell /usr/sbin/nologin appuser

WORKDIR /app

# Bring across the pre-built virtualenv from the builder stage.
COPY --from=builder /app/.venv /app/.venv

# Source tree. These are listed explicitly so the .dockerignore can't
# accidentally leak secrets or caches into the image.
COPY app/         ./app/
COPY alembic/     ./alembic/
COPY alembic.ini  ./alembic.ini
COPY scripts/     ./scripts/
COPY deploy/      ./deploy/
COPY pyproject.toml uv.lock ./

# Entrypoint must be executable; copy preserves perms on Linux but set
# explicitly so Windows/CRLF check-outs don't land broken images.
RUN chmod +x scripts/entrypoint.sh

# Run as the unprivileged user. Everything under /app should be readable.
RUN chown -R appuser:appuser /app
USER appuser

# Put the venv's bin first so `alembic`, `uvicorn`, `python -m app.sync.cli`
# all resolve without repeating the path.
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONPATH="/app" \
    PYTHONUNBUFFERED=1

EXPOSE 8000

# Container-level liveness. The /health endpoint is a skip-path for rate
# limiting so repeated probes don't consume buckets. Two-second start
# period lets the Python process come up before the first probe fires.
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl --fail --silent --show-error http://localhost:8000/health || exit 1

# tini reaps zombies and forwards signals properly to uvicorn; the
# entrypoint script then `exec`s uvicorn so PID 1 is Python.
ENTRYPOINT ["/usr/bin/tini", "--", "/app/scripts/entrypoint.sh"]
