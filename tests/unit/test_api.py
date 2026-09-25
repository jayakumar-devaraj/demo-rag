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
