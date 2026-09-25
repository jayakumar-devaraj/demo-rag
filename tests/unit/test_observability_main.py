"""Unit tests for the JSON audit log format, tracing opt-in, and provider wiring without credentials."""

import json
import logging

from src.main import build_services
from src.utils.observability import JsonFormatter, trace_callbacks


def test_json_log_line_carries_extra_fields():
    record = logging.makeLogRecord({"name": "audit", "levelname": "INFO", "msg": "page_written",
                                    "tenant_id": "acme", "status": "replaced"})
    line = json.loads(JsonFormatter().format(record))
    assert line["event"] == "page_written" and line["tenant_id"] == "acme" and line["status"] == "replaced"


def test_tracing_disabled_without_langfuse_keys(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    assert trace_callbacks() == []


def test_no_provider_configured_leaves_services_empty(monkeypatch):
    for k in ("OPENAI_API_KEY", "OPENAI_CHAT_MODEL", "OPENAI_EMBEDDING_MODEL"):
        monkeypatch.delenv(k, raising=False)
    s = build_services()
    assert s.embedder is None and s.llm is None and s.triad is None


def test_partial_provider_config_is_treated_as_unconfigured(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "placeholder-not-a-real-key")
    monkeypatch.delenv("OPENAI_CHAT_MODEL", raising=False)
    assert build_services().llm is None
