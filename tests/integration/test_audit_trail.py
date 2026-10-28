"""Audit trail on a real Postgres: rows for ingest, retrieve and chat, tenant scoping, atomicity, fail-closed."""

import asyncio
import sys

import psycopg
import pytest
from fastapi.testclient import TestClient

from src.agents.synthesis import Answer, SufficiencyVerdict
from src.db.connection import database_url, run
from src.routers.api import Services, create_app
from tests.conftest import FakeEmbedder, ScriptedLLM, make_xlsx
from tests.migration_support import scratch_database, unique, upgrade_head, with_credentials

pytestmark = pytest.mark.integration

if sys.platform == "win32":  # psycopg async needs a selector loop; TestClient's portal uses the policy default
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

TRACEPARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
A, B = {"Authorization": "Bearer tok-a"}, {"Authorization": "Bearer tok-b"}
XLSX = make_xlsx({"Q1": [["Region", "Sales"], ["North", 100]], "Q2": [["Region", "Sales"], ["South", 200]]})


def _llm(*, sufficient: bool = True) -> ScriptedLLM:
    return ScriptedLLM(SufficiencyVerdict=[SufficiencyVerdict(sufficient=sufficient)] * 3,
                       Answer=[Answer(answer="North sales were 100.")] * 3)


def _app(llm=None, embedder=None) -> TestClient:
    return TestClient(create_app(Services(embedder=embedder or FakeEmbedder(), llm=llm or _llm(), triad=None)))


def _trail(client: TestClient, headers: dict, **params) -> dict:
    r = client.get("/audit", headers=headers, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_ingest_retrieve_and_chat_are_audited_and_tenant_scoped(monkeypatch, tenant):
    monkeypatch.setenv("TENANT_TOKENS", f"tok-a:{tenant},tok-b:{tenant}-other")
    with _app() as client:
        hdr = {**A, "X-Request-ID": "req-audit-1", "traceparent": TRACEPARENT}
        assert client.post("/ingest", files={"file": ("s.xlsx", XLSX)}, headers=hdr).status_code == 200
        assert client.post("/v1/retrieve", json={"question": "North sales"}, headers=A).status_code == 200
        assert client.post("/chat", json={"question": "North sales"}, headers=A).status_code == 200

        page = _trail(client, A)
        assert [e["action"] for e in page["entries"]] == ["chat", "retrieve", "ingest"]  # newest first
        assert all(e["outcome"] == "ok" and e["subject"].startswith("token:") for e in page["entries"])
        ingest = page["entries"][-1]
        assert ingest["doc_key"] == "s.xlsx" and ingest["request_id"] == "req-audit-1"
        assert ingest["traceparent"] == TRACEPARENT
        assert ingest["detail"] == {"filename": "s.xlsx", "units": 2, "purged_pages": 0}
        assert "North sales" not in str(page)  # question text is never stored

        assert _trail(client, B)["entries"] == []  # another tenant sees nothing of tenant a's trail
        assert [e["action"] for e in _trail(client, A, action="retrieve")["entries"]] == ["retrieve"]

        first = _trail(client, A, limit=2)
        assert len(first["entries"]) == 2 and first["next_before_id"] == first["entries"][-1]["id"]
        rest = _trail(client, A, limit=2, before_id=first["next_before_id"])
        assert [e["action"] for e in rest["entries"]] == ["ingest"] and rest["next_before_id"] is None

        assert client.get("/audit").status_code == 401
        assert client.get("/audit", headers=A, params={"limit": 0}).status_code == 422


class _FailsOnSecondPage(FakeEmbedder):
    """TEST DOUBLE: embedding fails for the second tab with a content error (→ 422)."""

    async def embed(self, texts):
        if self.calls:
            raise ValueError("bad second page")
        return await super().embed(texts)


def test_failed_ingest_leaves_no_partial_document_and_an_error_row(monkeypatch, tenant):
    monkeypatch.setenv("TENANT_TOKENS", f"tok-a:{tenant}")
    with _app(embedder=_FailsOnSecondPage()) as client:
        r = client.post("/ingest", files={"file": ("s.xlsx", XLSX)}, headers=A)
        assert r.status_code == 422
        entries = _trail(client, A)["entries"]
    assert [(e["action"], e["outcome"], e["detail"]) for e in entries] == [("ingest", "error", {"error": "ValueError"})]
    pages = run(_count(database_url(), "SELECT count(*) FROM page_registry WHERE tenant_id = %s", tenant))
    assert pages == 0  # the first tab was rolled back with the failed document


def test_no_audit_row_means_ingest_is_rolled_back(monkeypatch, tenant):
    monkeypatch.setenv("TENANT_TOKENS", f"tok-a:{tenant}")

    async def failing_audit(conn, session, action, outcome, doc_key=None, detail=None):
        raise psycopg.errors.InsufficientPrivilege("denied")

    monkeypatch.setattr("src.routers.api.record_audit", failing_audit)
    with TestClient(create_app(Services(embedder=FakeEmbedder(), llm=_llm(), triad=None)),
                    raise_server_exceptions=False) as client:
        assert client.post("/ingest", files={"file": ("s.xlsx", XLSX)}, headers=A).status_code == 500
    assert run(_count(database_url(), "SELECT count(*) FROM page_registry WHERE tenant_id = %s", tenant)) == 0


def test_whole_flow_works_as_the_non_owner_app_role(monkeypatch, tenant, conn):
    role, password = unique("app"), unique("pw")
    run(conn.execute(f"CREATE ROLE {role} LOGIN PASSWORD '{password}' NOSUPERUSER NOCREATEDB NOCREATEROLE"))
    try:
        with scratch_database(conn, database_url()) as owner_url:
            upgrade_head(owner_url, app_role=role)
            monkeypatch.setenv("DATABASE_URL", with_credentials(owner_url, role, password))
            monkeypatch.setenv("TENANT_TOKENS", f"tok-a:{tenant}")
            with _app() as client:  # startup revision check also runs as the app role
                assert client.post("/ingest", files={"file": ("s.xlsx", XLSX)}, headers=A).status_code == 200
                assert client.post("/chat", json={"question": "North sales"}, headers=A).status_code == 200
                assert [e["action"] for e in _trail(client, A)["entries"]] == ["chat", "ingest"]
    finally:
        run(conn.execute(f"DROP ROLE IF EXISTS {role}"))


async def _count(url: str, sql: str, *params: str) -> int:
    async with await psycopg.AsyncConnection.connect(url, autocommit=True) as c:
        return (await (await c.execute(sql, params)).fetchone())[0]
