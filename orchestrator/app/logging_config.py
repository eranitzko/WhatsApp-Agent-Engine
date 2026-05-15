"""Logging configuration.

In development (LOG_FORMAT=text, the default): human-readable coloured output.
In production (LOG_FORMAT=json): newline-delimited JSON, one record per line.
Fly.io ships stdout/stderr to its log aggregation — JSON makes them queryable.

Call configure_logging() once at startup, before any loggers are used.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time


class _JsonFormatter(logging.Formatter):
    """Emit each log record as a single JSON object on stdout."""

    def format(self, record: logging.LogRecord) -> str:
        obj = {
            "ts":      time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level":   record.levelname,
            "logger":  record.name,
            "msg":     record.getMessage(),
        }
        if record.exc_info:
            obj["exc"] = self.formatException(record.exc_info)
        # Include any extra fields attached via logger.info("...", extra={...})
        for key, val in record.__dict__.items():
            if key not in logging.LogRecord.__dict__ and not key.startswith("_"):
                obj[key] = val
        return json.dumps(obj, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Set up root logger. Call once at app startup."""
    fmt = os.environ.get("LOG_FORMAT", "text").lower()
    log_level = getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(log_level)

    # Remove any handlers already attached (e.g. from basicConfig called elsewhere)
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(log_level)

    if fmt == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%SZ",
            )
        )

    root.addHandler(handler)

    # Silence noisy third-party loggers
    for noisy in ("httpx", "httpcore", "hpack", "aiobotocore", "boto3", "botocore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
