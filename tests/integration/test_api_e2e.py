"""Rung-2 end-to-end: HTTP ingest + chat through the graph against real Postgres (LLM is a test double)."""

import asyncio
import sys

import pytest
from fastapi.testclient import TestClient

from src.agents.synthesis import Answer, SufficiencyVerdict
from src.routers.api import Services, create_app
from tests.conftest import FakeEmbedder, ScriptedLLM, make_docx, make_xlsx

pytestmark = pytest.mark.integration

if sys.platform == "win32":  # psycopg async needs a selector loop; TestClient's portal uses the policy default
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def test_ingest_then_self_correcting_chat_across_docx_and_xlsx(monkeypatch, tenant):
    monkeypatch.setenv("TENANT_TOKENS", f"tok-a:{tenant},tok-b:{tenant}-other")
    llm = ScriptedLLM(
        SufficiencyVerdict=[SufficiencyVerdict(sufficient=False, missing_identifiers=["Servers"]),
                            SufficiencyVerdict(sufficient=True)],
        Answer=[Answer(answer="Servers cost 4200; north sales were 100.", citations=["r.docx#page:1"])])
    client = TestClient(create_app(Services(embedder=FakeEmbedder(), llm=llm, triad=None)))
    a, b = {"Authorization": "Bearer tok-a"}, {"Authorization": "Bearer tok-b"}

    xlsx = make_xlsx({"Q1": [["Region", "Sales"], ["North", 100]]})
    assert client.post("/ingest", files={"file": ("r.docx", make_docx())}, headers=a).status_code == 200
    assert client.post("/ingest", files={"file": ("s.xlsx", xlsx)}, headers=a).status_code == 200

    r = client.post("/chat", json={"question": "North sales vs budget"}, headers=a)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["queries_run"] == ["North sales vs budget", "Servers"] and body["attempts"] == 2
    assert body["triad"]["numeric_groundedness"] == 1.0 and not body["insufficient"]

    other = client.post("/chat", json={"question": "North sales vs budget"}, headers=b)
    assert other.status_code == 200 and other.json()["insufficient"]  # tenant b sees none of tenant a's data
