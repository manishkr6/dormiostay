"""Structured JSON logging for API and infrastructure events."""

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any


class JsonFormatter(logging.Formatter):
    """Render log records as single-line JSON without serialising secrets."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": "dormiostay",
            "message": "Application error"
            if record.levelno >= logging.ERROR
            else "Application event",
            "code": "INTERNAL_SERVER_ERROR" if record.levelno >= logging.ERROR else "APP_EVENT",
        }
        request_id = getattr(record, "request_id", None)
        if isinstance(request_id, str) and re.fullmatch(r"[0-9a-f-]{36}", request_id):
            payload["request_id"] = request_id
        # Never serialize arbitrary messages, SQL, parameters, traceback locals,
        # or driver exception strings. These can contain credentials and PII.
        return json.dumps(payload, separators=(",", ":"), default=str)


def configure_logging() -> None:
    """Install the process-wide JSON handler once."""

    root = logging.getLogger()
    if not any(isinstance(handler.formatter, JsonFormatter) for handler in root.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        root.handlers.clear()
        root.addHandler(handler)
    root.setLevel(logging.INFO)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "sqlalchemy", "asyncpg"):
        child = logging.getLogger(name)
        child.handlers.clear()
        child.propagate = True
