"""Unit tests for API boundary behaviour that needs no database."""

from fastapi.testclient import TestClient

from src.routers.api import Services, create_app
from tests.conftest import FakeEmbedder, ScriptedLLM

AUTH = {"Authorization": "Bearer tok-a"}


def _client(monkeypatch, **services) -> TestClient:
    monkeypatch.setenv("TENANT_TOKENS", "tok-a:tenant-a")
    base = {"embedder": FakeEmbedder(), "llm": ScriptedLLM(), "triad": None}
    return TestClient(create_app(Services(**(base | services))))


def test_missing_or_bad_token_is_401(monkeypatch):
    c = _client(monkeypatch)
    assert c.post("/chat", json={"question": "q"}).status_code == 401
    assert c.post("/chat", json={"question": "q"}, headers={"Authorization": "Bearer nope"}).status_code == 401


def test_tenant_field_in_body_is_not_accepted_as_identity(monkeypatch):
    assert _client(monkeypatch).post("/chat", json={"question": "q", "tenant_id": "tenant-a"}).status_code == 401


def test_unsupported_upload_is_415(monkeypatch):
    r = _client(monkeypatch).post("/ingest", files={"file": ("a.pptx", b"x")}, headers=AUTH)
    assert r.status_code == 415


def test_unconfigured_provider_is_503(monkeypatch):
    c = _client(monkeypatch, llm=None, embedder=None)
    assert c.post("/chat", json={"question": "q"}, headers=AUTH).status_code == 503
    assert c.post("/ingest", files={"file": ("a.txt", b"x")}, headers=AUTH).status_code == 503


def test_empty_question_is_422(monkeypatch):
    assert _client(monkeypatch).post("/chat", json={"question": ""}, headers=AUTH).status_code == 422


class _QuotaExhaustedEmbedder:
    """TEST DOUBLE: raises the error OpenAI returns when the account has no credits."""

    async def embed(self, texts):
        import httpx
        import openai

        response = httpx.Response(429, request=httpx.Request("POST", "https://api.openai.com/v1/embeddings"))
        raise openai.RateLimitError("insufficient_quota", response=response, body=None)


def test_provider_quota_error_is_503_not_500(monkeypatch):
    import asyncio
    import sys

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    monkeypatch.setattr("src.routers.api._connect", _fake_connect)
    c = _client(monkeypatch, embedder=_QuotaExhaustedEmbedder())
    r = c.post("/ingest", files={"file": ("a.txt", b"hello")}, headers=AUTH)
    assert r.status_code == 503 and r.json()["detail"] == "model provider unavailable"


class _NoDbConn:
    """TEST DOUBLE: connection whose lookups find nothing, so ingest proceeds to embedding."""

    async def execute(self, *args, **kwargs):
        class _Cur:
            async def fetchone(self):
                return None

        return _Cur()

    async def close(self):
        return None


async def _fake_connect():
    return _NoDbConn()
