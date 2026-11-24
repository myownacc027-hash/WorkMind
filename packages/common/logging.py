from __future__ import annotations

import logging
import sys

import structlog


def _configure_utf8_stdio() -> None:
    """Keep redirected Windows console/log streams Unicode-safe.

    Windows Python may default to a legacy code page when stdout/stderr
    are redirected. Structured logs include Outlook subjects, attachment
    filenames, and other text that may contain characters outside it.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
        except (OSError, ValueError, TypeError):
            # Nonstandard capture streams may not support reconfiguration.
            pass


def configure_logging(level: str = "INFO") -> None:
    _configure_utf8_stdio()
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(stream=sys.stdout, level=numeric_level, format="%(message)s")

    # Keep WorkMind events visible but suppress one line per internal HTTP call.
    for logger_name in (
        "httpx",
        "httpcore",
        "huggingface_hub",
        "qdrant_client",
    ):
        logging.getLogger(logger_name).setLevel(logging.WARNING)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=False),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
