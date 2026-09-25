"""Unit tests for the JSON audit log format, tracing opt-in, and provider wiring without credentials."""

import json
import logging

from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel

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


PROVIDER_VARS = ("GROQ_API_KEY", "GROQ_CHAT_MODEL", "OPENAI_API_KEY", "OPENAI_CHAT_MODEL",
                 "OPENAI_EMBEDDING_MODEL", "FASTEMBED_MODEL")


def _clear_providers(monkeypatch):
    for k in PROVIDER_VARS:
        monkeypatch.delenv(k, raising=False)


def test_no_provider_configured_leaves_services_empty(monkeypatch):
    _clear_providers(monkeypatch)
    s = build_services()
    assert s.embedder is None and s.llm is None and s.triad is None


def test_partial_provider_config_is_treated_as_unconfigured(monkeypatch):
    _clear_providers(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "placeholder-not-a-real-key")
    assert build_services().llm is None


def test_groq_is_primary_and_openai_is_fallback(monkeypatch):
    _clear_providers(monkeypatch)
    monkeypatch.setenv("GROQ_API_KEY", "placeholder-not-a-real-key")
    monkeypatch.setenv("GROQ_CHAT_MODEL", "groq-model")
    monkeypatch.setenv("OPENAI_API_KEY", "placeholder-not-a-real-key")
    monkeypatch.setenv("OPENAI_CHAT_MODEL", "openai-model")
    llm = build_services().llm
    assert type(llm.primary).__name__ == "ChatGroq" and llm.primary.model_name == "groq-model"
    assert type(llm.fallback).__name__ == "ChatOpenAI" and llm.fallback.model_name == "openai-model"


def test_fallback_answers_when_primary_fails():
    from src.db.connection import run
    from src.main import FallbackLLM

    class _Out(BaseModel):
        value: str

    class _Down:
        def with_structured_output(self, schema):
            return RunnableLambda(lambda _: (_ for _ in ()).throw(RuntimeError("primary down")))

    class _Up:
        def with_structured_output(self, schema):
            return RunnableLambda(lambda _: schema(value="from fallback"))

    out = run(FallbackLLM(_Down(), _Up()).with_structured_output(_Out).ainvoke("q"))
    assert out.value == "from fallback"
