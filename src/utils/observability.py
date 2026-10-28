"""Structured JSON logging and optional Langfuse tracing."""

import json
import logging
import os
import re
import uuid
from contextvars import ContextVar
from typing import Any

_RESERVED = set(vars(logging.makeLogRecord({})))

request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_base_factory = logging.getLogRecordFactory()

traceparent: ContextVar[str | None] = ContextVar("traceparent", default=None)
_VALID_TRACEPARENT = re.compile(r"^[0-9a-f]{2}-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$")


def resolve_traceparent(incoming: str | None) -> str | None:
    """Keep a caller's W3C traceparent only if it is well formed; it is stored in the audit trail."""
    return incoming if incoming and _VALID_TRACEPARENT.match(incoming) else None


def resolve_request_id(incoming: str | None) -> str:
    """Accept a caller's X-Request-ID if it is short and safe to log; otherwise mint a new one."""
    return incoming if incoming and _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex


def _record_with_request_id(*args: Any, **kwargs: Any) -> logging.LogRecord:
    record = _base_factory(*args, **kwargs)
    rid = request_id.get()
    if rid is not None:
        record.request_id = rid
    return record


def install_request_id_logging() -> None:
    """Stamp the current request id onto every log record, whatever handler formats it (idempotent)."""
    logging.setLogRecordFactory(_record_with_request_id)


class JsonFormatter(logging.Formatter):
    """One JSON object per line: timestamp, level, logger, event, plus any `extra` fields."""

    def format(self, record: logging.LogRecord) -> str:
        """Serialize a record."""
        payload = {"ts": self.formatTime(record), "level": record.levelname, "logger": record.name,
                   "event": record.getMessage()}
        payload.update({k: v for k, v in vars(record).items() if k not in _RESERVED})
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Route the root logger through the JSON formatter."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)


def trace_callbacks() -> list[Any]:
    """Langfuse LangChain callback when LANGFUSE_PUBLIC_KEY/SECRET_KEY are set, else none.

    Without keys, the per-node structured-log spans in src/orchestration/graph.py are the trace.
    """
    if not (os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")):
        return []
    from langfuse.langchain import CallbackHandler  # noqa: PLC0415 - only imported when configured

    return [CallbackHandler()]  # VERIFY: Langfuse 4.15.6 handler against a live Langfuse (no keys here)
