"""Structured logging helpers for runtime observability."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.engine import make_url

_RESERVED_ATTRS = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "message",
    "asctime",
}


class JsonFormatter(logging.Formatter):
    """Format log records as JSON for easier parsing in production."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)

        for key, value in record.__dict__.items():
            if key in _RESERVED_ATTRS or key.startswith("_"):
                continue
            payload[key] = value

        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str | None = None) -> None:
    """Configure root logger once with JSON output."""
    root = logging.getLogger()
    if getattr(root, "_rednote_logging_configured", False):
        return

    log_level = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())

    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(log_level)
    setattr(root, "_rednote_logging_configured", True)


def describe_database_target(database_url: str) -> dict[str, str]:
    try:
        url = make_url(database_url)
        database_name = url.database or "<unknown>"
        if url.drivername.startswith("sqlite") and database_name not in {"", ":memory:"}:
            database_name = database_name.replace('\\', '/').rsplit('/', 1)[-1]
        payload = {
            "database_backend": url.drivername,
            "database_name": database_name,
        }
        if url.host:
            payload["database_host"] = url.host
        return payload
    except Exception:  # noqa: BLE001
        database_name = (database_url or "").replace('\\', '/').rsplit('/', 1)[-1] or "<unknown>"
        return {
            "database_backend": "unknown",
            "database_name": database_name,
        }


def log_database_target(logger: logging.Logger, *, database_url: str, source: str) -> None:
    logger.info(
        "database_target",
        extra={
            "event": "database_target",
            "source": source,
            **describe_database_target(database_url),
        },
    )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
