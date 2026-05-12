#!/usr/bin/env bash
# API entrypoint.
#
# 1. Apply Alembic migrations (baseline is a no-op, future V2-owned
#    migrations run here). Safe to run against any DB stamped at a
#    revision already — alembic will simply report "nothing to do".
# 2. exec uvicorn so it becomes PID of this shell — ensuring Docker's
#    SIGTERM reaches uvicorn and graceful shutdown happens in <10s.
#
# For the sync worker the image is the same; override the command to:
#   python -m app.sync.cli reindex incremental
#   python -m app.sync.cli reindex full
#   python -m app.sync.cli init-index

set -euo pipefail

# When the DB was created out-of-band by ETL, the alembic_version table
# is empty on first run. `alembic upgrade head` over an empty version
# table still applies our no-op baseline, which is exactly what we want
# on fresh environments. If you prefer the "stamp-only" flow (the
# schema exists and you never want migrations run), set
# ALEMBIC_MODE=stamp in the environment.
case "${ALEMBIC_MODE:-upgrade}" in
  upgrade)
    echo "[entrypoint] alembic upgrade head"
    alembic upgrade head
    ;;
  stamp)
    echo "[entrypoint] alembic stamp head"
    alembic stamp head
    ;;
  skip)
    echo "[entrypoint] ALEMBIC_MODE=skip — leaving schema untouched"
    ;;
  *)
    echo "[entrypoint] unknown ALEMBIC_MODE='${ALEMBIC_MODE}' (expected: upgrade|stamp|skip)" >&2
    exit 2
    ;;
esac

echo "[entrypoint] exec uvicorn"
exec uvicorn app.main:app \
     --host 0.0.0.0 \
     --port 8000 \
     --proxy-headers \
     --forwarded-allow-ips='*'
