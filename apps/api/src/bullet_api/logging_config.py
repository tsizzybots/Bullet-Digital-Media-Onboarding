"""Structured JSON logging for the Bullet API.

Render's log viewer treats each line as a record; emitting JSON gives us
field-by-field search (`level=ERROR`, `request_id=...`, `client_id=...`)
without needing an external aggregator. Use the stdlib `logging` module
through this configuration so any library that already logs (httpx,
sqlalchemy, asyncpg, uvicorn) lands in the same JSON stream.

`configure_logging()` is idempotent so a re-import (FastAPI test client,
worker re-entry, etc.) does not stack handlers.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

# stdlib LogRecord attributes - anything else on `record.__dict__` was
# added by the caller via `logger.info("msg", extra={"k": v})` and is
# safe to surface in the JSON payload.
_BUILTIN_LOG_RECORD_KEYS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


class JsonFormatter(logging.Formatter):
    """Render LogRecords as one-line JSON. Keeps the field set small so
    operators reading Render logs do not have to scroll past noise."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=UTC
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        # Surface anything attached via `logger.info("msg", extra={...})`.
        for key, value in record.__dict__.items():
            if key not in _BUILTIN_LOG_RECORD_KEYS and not key.startswith("_"):
                payload[key] = value
        return json.dumps(payload, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    """Install the JSON formatter on the root logger.

    Idempotent: removes any previously-installed handlers first so re-entry
    in tests / workers does not duplicate output.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level)
    # Quiet down uvicorn's access log defaults; the JSON access log emitted
    # by uvicorn itself (when `--access-log` is on) flows through this
    # handler unchanged.
    logging.getLogger("uvicorn.error").propagate = True
    logging.getLogger("uvicorn.access").propagate = True
