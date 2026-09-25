"""Structured JSON logging and optional Langfuse tracing."""

import json
import logging
import os
from typing import Any

_RESERVED = set(vars(logging.makeLogRecord({})))


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
