"""Structured JSON logging via structlog, with stdlib logging interop."""
from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(level: str = "INFO") -> None:
    """Configure structlog to emit JSON logs to stdout.

    Also routes the standard `logging` module through structlog so third-party
    libraries (uvicorn, sqlalchemy, httpx, etc.) produce the same shape.
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    # Configure stdlib logging: one handler, JSON output via structlog's formatter
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    # Clear default handlers (uvicorn adds its own in dev; we want JSON only).
    root.handlers = [handler]
    root.setLevel(log_level)

    # Align uvicorn noisy loggers with our formatter.
    for noisy in ("uvicorn", "uvicorn.error", "uvicorn.access", "sqlalchemy.engine"):
        lg = logging.getLogger(noisy)
        lg.handlers = [handler]
        lg.propagate = False
        lg.setLevel(log_level)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound logger. Safe to call before `configure_logging`."""
    return structlog.get_logger(name)
