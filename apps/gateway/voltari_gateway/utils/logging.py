"""Structured logging configuration via structlog.

Output is JSON in production and pretty-printed in local development.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from voltari_gateway.config import get_settings


def configure_logging() -> None:
    """Configure structlog and stdlib logging once at startup."""
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if settings.app_env.is_dev_like:
        renderer: Any = structlog.dev.ConsoleRenderer(colors=True)
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    # structlog.get_logger() возвращает FilteringBoundLogger который для нашего
    # use-case ведёт себя как BoundLogger (мы используем стандартные методы:
    # info/warning/error/exception + bind/contextvars). Cast — чтобы mypy строго
    # подтверждал интерфейс на consumer-стороне.
    return structlog.get_logger(name)  # type: ignore[no-any-return]
