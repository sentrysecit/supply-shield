"""Structured logging configuration built on structlog."""

import logging

import structlog
from structlog.typing import Processor

from supply_shield.config import Settings


def configure_logging(settings: Settings) -> None:
    """Configure structlog: JSON lines in production, readable console output in development."""
    processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    if settings.environment == "production":
        processors += [structlog.processors.dict_tracebacks, structlog.processors.JSONRenderer()]
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[settings.log_level]
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
