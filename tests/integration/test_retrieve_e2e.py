"""Rung-2: POST /v1/retrieve against real Postgres+pgvector (LLM is a test double)."""

import asyncio
import sys

import pytest
from fastapi.testclient import TestClient

from src.agents.synthesis import SufficiencyVerdict
from src.routers.api import Services, create_app
from tests.conftest import FakeEmbedder, ScriptedLLM, make_docx, make_xlsx

pytestmark = pytest.mark.integration

if sys.platform == "win32":  # psycopg async needs a selector loop; TestClient's portal uses the policy default
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def test_retrieve_is_tenant_isolated(monkeypatch, tenant):
    monkeypatch.setenv("TENANT_TOKENS", f"tok-a:{tenant},tok-b:{tenant}-other")
    a, b = {"Authorization": "Bearer tok-a"}, {"Authorization": "Bearer tok-b"}
    lean = ScriptedLLM(SufficiencyVerdict=[SufficiencyVerdict(sufficient=True)])
    with TestClient(create_app(Services(embedder=FakeEmbedder(), llm=ScriptedLLM(), triad=None,
                                        retrieve_llm=lean))) as client:
        assert client.post("/ingest", files={"file": ("r.docx", make_docx())}, headers=a).status_code == 200
        secret = make_xlsx({"Private": [["Codename", "Value"], ["Falcon", 777]]})
        assert client.post("/ingest", files={"file": ("s.xlsx", secret)}, headers=b).status_code == 200

        own = client.post("/v1/retrieve", json={"question": "Servers cost"}, headers=a)
        assert own.status_code == 200, own.text
        body = own.json()
        assert not body["insufficient"] and body["attempts"] == 1
        assert any("4200" in s["markdown"] for s in body["sections"])
        assert not any("777" in s["markdown"] for s in body["sections"])  # tenant b's row never reaches a

        # tenant c has ingested nothing: no sections, insufficient, and no LLM call (lean has no verdict left)
        monkeypatch.setenv("TENANT_TOKENS", f"tok-a:{tenant},tok-b:{tenant}-other,tok-c:{tenant}-empty")
        empty = client.post("/v1/retrieve", json={"question": "Servers cost"},
                            headers={"Authorization": "Bearer tok-c"})
        assert empty.status_code == 200
        assert empty.json() == {"sections": [], "insufficient": True, "queries_run": ["Servers cost"], "attempts": 1}
