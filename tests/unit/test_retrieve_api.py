"""Unit tests for POST /v1/retrieve: auth, status mapping, deadline, retry policy (no DB, doubles at the seams)."""

import asyncio
import time
from contextlib import asynccontextmanager

import httpx
import openai
import psycopg
import pytest
from fastapi.testclient import TestClient

from src.agents.synthesis import SufficiencyVerdict
from src.orchestration.graph import MAX_ATTEMPTS
from src.retrieval.hybrid import ParentSection
from src.routers.api import RETRIEVE_MAX_TIMEOUT_MS, Services, create_app
from tests.conftest import FakeEmbedder, ScriptedLLM

AUTH = {"Authorization": "Bearer tok-a"}
BODY = {"question": "what is the budget"}


def _section(pid: int, text: str = "Budget 4200") -> ParentSection:
    return ParentSection(page_id=pid, doc_key="d", page_key=f"page:{pid}", source_name="f.pdf",
                         markdown=text, score=1.0, chunk_ids=[pid])


class _Conn:
    """TEST DOUBLE: a connection that is only ever closed (retrieval itself is patched)."""

    async def close(self):
        return None


class _RaisingLLM:
    """TEST DOUBLE: every structured call raises `exc`."""

    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def with_structured_output(self, schema):
        exc = self.exc

        class _Runnable:
            async def ainvoke(self, messages, config=None):
                raise exc

        return _Runnable()


@pytest.fixture
def client(monkeypatch):
    """Factory for a TestClient whose DB transactions and `retrieve` are replaced; returns (client, tenants_seen).

    `make.held` counts the tenant transactions (pooled connections) open at any moment.
    """
    monkeypatch.setenv("TENANT_TOKENS", "tok-a:tenant-a")
    tenants: list[str] = []

    def make(retrieve=None, raise_server_exceptions=True, audit=None, **services) -> tuple[TestClient, list[str]]:
        async def default_retrieve(conn, embedder, session, query):
            tenants.append(session.tenant_id)
            return [_section(1)]

        @asynccontextmanager
        async def tenant_tx(request, tenant_id):
            make.held += 1
            try:
                yield _Conn()
            finally:
                make.held -= 1

        async def record_audit(conn, session, action, outcome, doc_key=None, detail=None):
            make.audits.append((session.tenant_id, action, outcome, detail))

        monkeypatch.setattr("src.routers.api._tenant_tx", tenant_tx)
        monkeypatch.setattr("src.routers.api.record_audit", audit or record_audit)
        monkeypatch.setattr("src.routers.api.retrieve", retrieve or default_retrieve)
        base = {"embedder": FakeEmbedder(), "llm": ScriptedLLM(), "triad": None}
        app = create_app(Services(**(base | services)))
        return TestClient(app, raise_server_exceptions=raise_server_exceptions), tenants

    make.audits = []
    make.held = 0
    return make


class _HeldConnectionsLLM:
    """TEST DOUBLE: answers every structured call from `scripts`, recording how many connections are held."""

    def __init__(self, held, **scripts: list) -> None:
        self.held, self.scripts, self.seen = held, {k: list(v) for k, v in scripts.items()}, []

    def with_structured_output(self, schema):
        llm = self

        class _Runnable:
            async def ainvoke(self, messages, config=None):
                llm.seen.append(llm.held())
                return llm.scripts[schema.__name__].pop(0)

        return _Runnable()


def test_no_connection_is_held_while_the_model_runs(client):
    """A model call can take seconds; a pooled connection held through it starves other requests."""
    from src.agents.synthesis import Answer

    make = client
    retrieve_llm = _HeldConnectionsLLM(lambda: make.held, SufficiencyVerdict=[SufficiencyVerdict(sufficient=True)])
    chat_llm = _HeldConnectionsLLM(lambda: make.held, SufficiencyVerdict=[SufficiencyVerdict(sufficient=True)],
                                   Answer=[Answer(answer="Budget 4200", insufficient=False, citations=["d"])])
    c, _ = make(llm=chat_llm, retrieve_llm=retrieve_llm)
    assert c.post("/v1/retrieve", json=BODY, headers=AUTH).status_code == 200
    assert c.post("/chat", json=BODY, headers=AUTH).status_code == 200
    assert retrieve_llm.seen == [0] and chat_llm.seen == [0, 0]
    assert make.held == 0


def test_missing_or_bad_token_is_401(client):
    c, _ = client()
    assert c.post("/v1/retrieve", json=BODY).status_code == 401
    assert c.post("/v1/retrieve", json=BODY, headers={"Authorization": "Bearer nope"}).status_code == 401


@pytest.mark.parametrize("body", [{"question": ""}, {"question": "x" * 2001}, {}])
def test_bad_body_is_422(client, body):
    c, _ = client()
    assert c.post("/v1/retrieve", json=body, headers=AUTH).status_code == 422


@pytest.mark.parametrize("value", ["0", "-5", "soon", str(RETRIEVE_MAX_TIMEOUT_MS + 1)])
def test_bad_deadline_header_is_422(client, value):
    c, _ = client()
    assert c.post("/v1/retrieve", json=BODY, headers={**AUTH, "X-Timeout-Ms": value}).status_code == 422


def test_tenant_comes_from_the_token_never_the_body(client):
    llm = ScriptedLLM(SufficiencyVerdict=[SufficiencyVerdict(sufficient=True)])
    c, tenants = client(llm=llm)
    r = c.post("/v1/retrieve", json={**BODY, "tenant_id": "tenant-b"}, headers=AUTH)
    assert r.status_code == 200 and tenants == ["tenant-a"]


def test_sufficient_context_returns_sections_and_writes_no_answer(client):
    llm = ScriptedLLM(SufficiencyVerdict=[SufficiencyVerdict(sufficient=True)])  # no Answer script: synthesis would fail
    c, _ = client(llm=llm)
    r = c.post("/v1/retrieve", json=BODY, headers=AUTH)
    assert r.status_code == 200
    assert r.json() == {"sections": [_section(1).model_dump()], "insufficient": False,
                        "queries_run": [BODY["question"]], "attempts": 1}


def test_successful_retrieve_is_audited_without_question_text(client):
    llm = ScriptedLLM(SufficiencyVerdict=[SufficiencyVerdict(sufficient=True)])
    c, _ = client(llm=llm)
    assert c.post("/v1/retrieve", json=BODY, headers=AUTH).status_code == 200
    assert client.audits == [("tenant-a", "retrieve", "ok",
                              {"attempts": 1, "queries": 1, "sections": 1, "insufficient": False})]
    assert BODY["question"] not in str(client.audits)


def test_provider_failure_is_audited_as_error(client):
    c, _ = client(llm=_RaisingLLM(_provider_error(openai.RateLimitError, 429)))
    assert c.post("/v1/retrieve", json=BODY, headers=AUTH).status_code == 503
    assert client.audits == [("tenant-a", "retrieve", "error", {"error": "RateLimitError"})]


def test_no_audit_row_means_no_data_is_released(client):
    async def failing_audit(conn, session, action, outcome, doc_key=None, detail=None):
        raise psycopg.errors.InsufficientPrivilege("denied")

    llm = ScriptedLLM(SufficiencyVerdict=[SufficiencyVerdict(sufficient=True)])
    c, _ = client(llm=llm, audit=failing_audit, raise_server_exceptions=False)
    r = c.post("/v1/retrieve", json=BODY, headers=AUTH)
    assert r.status_code == 500 and "sections" not in r.text


def test_insufficient_after_max_attempts_returns_no_sections(client):
    verdicts = [SufficiencyVerdict(sufficient=False, missing_identifiers=[f"X-{i}"]) for i in range(MAX_ATTEMPTS)]

    async def retrieve(conn, embedder, session, query):
        return [_section(abs(hash(query)) % 10_000)]

    c, _ = client(retrieve=retrieve, llm=ScriptedLLM(SufficiencyVerdict=verdicts))
    body = c.post("/v1/retrieve", json=BODY, headers=AUTH).json()
    assert body["insufficient"] and body["sections"] == [] and body["attempts"] == MAX_ATTEMPTS


def test_no_documents_is_insufficient_without_an_llm_call(client):
    async def retrieve(conn, embedder, session, query):
        return []

    llm = ScriptedLLM()
    c, _ = client(retrieve=retrieve, llm=llm)
    body = c.post("/v1/retrieve", json=BODY, headers=AUTH).json()
    assert body["insufficient"] and body["sections"] == [] and llm.prompts == []


def test_unconfigured_provider_is_503(client):
    c, _ = client(llm=None)
    assert c.post("/v1/retrieve", json=BODY, headers=AUTH).status_code == 503
    c, _ = client(embedder=None)
    assert c.post("/v1/retrieve", json=BODY, headers=AUTH).status_code == 503


def _provider_error(cls, status: int, **kw):
    response = httpx.Response(status, request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"))
    return cls("boom", response=response, body=None, **kw)


def test_provider_error_is_503(client):
    c, _ = client(llm=_RaisingLLM(_provider_error(openai.RateLimitError, 429)))
    r = c.post("/v1/retrieve", json=BODY, headers=AUTH)
    assert r.status_code == 503 and r.json()["detail"] == "model provider unavailable"


def test_database_error_is_503(client):
    async def retrieve(conn, embedder, session, query):
        raise psycopg.OperationalError("down")

    c, _ = client(retrieve=retrieve)
    r = c.post("/v1/retrieve", json=BODY, headers=AUTH)
    assert r.status_code == 503 and r.json()["detail"] == "database unavailable"


@pytest.mark.parametrize("exc", [openai.APITimeoutError(request=httpx.Request("POST", "https://x")), TimeoutError()])
def test_model_timeout_is_504(client, exc):
    c, _ = client(llm=_RaisingLLM(exc))
    assert c.post("/v1/retrieve", json=BODY, headers=AUTH).status_code == 504


def test_unexpected_error_is_500(client):
    c, _ = client(llm=_RaisingLLM(RuntimeError("bug")), raise_server_exceptions=False)
    assert c.post("/v1/retrieve", json=BODY, headers=AUTH).status_code == 500


def test_caller_deadline_is_honoured(client):
    async def slow_retrieve(conn, embedder, session, query):
        await asyncio.sleep(10)

    c, _ = client(retrieve=slow_retrieve)
    start = time.perf_counter()
    r = c.post("/v1/retrieve", json=BODY, headers={**AUTH, "X-Timeout-Ms": "100"})
    assert r.status_code == 504 and time.perf_counter() - start < 3


def test_retrieve_uses_the_lean_model_not_the_chat_model(client):
    chat, lean = ScriptedLLM(), ScriptedLLM(SufficiencyVerdict=[SufficiencyVerdict(sufficient=True)])
    c, _ = client(llm=chat, retrieve_llm=lean)
    assert c.post("/v1/retrieve", json=BODY, headers=AUTH).status_code == 200
    assert chat.prompts == [] and len(lean.prompts) == 1


def test_build_services_lowers_provider_retries_only_for_retrieve(monkeypatch):
    from src import main

    monkeypatch.setattr(main, "_embeddings", lambda: None)
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setenv("GROQ_CHAT_MODEL", "m")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("RETRIEVE_LLM_MAX_RETRIES", raising=False)
    s = main.build_services()
    assert (s.llm.primary.max_retries, s.retrieve_llm.primary.max_retries) == (2, 0)
    monkeypatch.setenv("RETRIEVE_LLM_MAX_RETRIES", "1")
    assert main.build_services().retrieve_llm.primary.max_retries == 1
