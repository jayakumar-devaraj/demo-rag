"""Rung-2 live verification: the three assignment scenarios against real OpenAI models and real Postgres.

Selected explicitly with `-m live` (needs OPENAI_API_KEY, OPENAI_CHAT_MODEL, OPENAI_EMBEDDING_MODEL);
excluded from the default run and from CI, which have no credentials.
"""

import asyncio
import sys

import pytest
from fastapi.testclient import TestClient

from src.main import build_services
from src.routers.api import create_app
from tests.conftest import make_docx, make_pdf, make_xlsx

pytestmark = [pytest.mark.integration, pytest.mark.live]

if sys.platform == "win32":  # psycopg async needs a selector loop; TestClient's portal uses the policy default
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.fixture
def client(monkeypatch, tenant):
    monkeypatch.setenv("TENANT_TOKENS", f"live:{tenant}")
    services = build_services()
    assert services.llm is not None, "live tests need OPENAI_API_KEY, OPENAI_CHAT_MODEL, OPENAI_EMBEDDING_MODEL"
    with TestClient(create_app(services)) as c:
        c.headers["Authorization"] = "Bearer live"
        yield c


def _assert_triad_scored(body: dict) -> None:
    triad = body["triad"]
    for k in ("context_relevance", "groundedness", "answer_relevance"):
        assert isinstance(triad[k], float) and 0.0 <= triad[k] <= 1.0, (k, triad)


def test_live_multitab_excel_cell_lookup(client):
    xlsx = make_xlsx({"Ledger": [["Invoice", "Amount"]] + [[f"INV-{i:04d}", i * 10] for i in range(80)],
                      "Vendors": [["Vendor", "Country"], ["Acme", "Canada"], ["Globex", "Peru"]]})
    assert client.post("/ingest", files={"file": ("ledger.xlsx", xlsx)}).status_code == 200
    body = client.post("/chat", json={"question": "What is the amount for invoice INV-0072?"}).json()
    print("\nSCENARIO 1:", body)
    assert not body["insufficient"] and "720" in body["answer"]
    _assert_triad_scored(body)


def test_live_renamed_pdf_modified_row(client):
    pages = [("Ledger page one", [["ID", "Amount"], ["INV-001", "250"]]),
             ("Ledger page two", [["ID", "Amount"], ["INV-002", "900"]])]
    first = client.post("/ingest", files={"file": ("ledger.pdf", make_pdf(pages))}).json()
    modified = [pages[0], ("Ledger page two", [["ID", "Amount"], ["INV-002", "999"]])]
    second = client.post("/ingest", files={"file": ("ledger_FINAL_v2.pdf", make_pdf(modified))}).json()
    print("\nSCENARIO 2 ingest:", [u["status"] for u in first["units"]], "->", [u["status"] for u in second["units"]])
    assert [u["status"] for u in second["units"]] == ["skipped", "replaced"]
    body = client.post("/chat", json={"question": "What is the amount for INV-002?"}).json()
    print("SCENARIO 2 chat:", body)
    assert "999" in body["answer"] and "900" not in body["answer"]


def test_live_ambiguous_query_across_docx_and_xlsx(client):
    xlsx = make_xlsx({"Q1": [["Region", "Sales"], ["North", 100], ["South", 80]]})
    assert client.post("/ingest", files={"file": ("r.docx", make_docx())}).status_code == 200
    assert client.post("/ingest", files={"file": ("s.xlsx", xlsx)}).status_code == 200
    body = client.post("/chat", json={
        "question": "Compare North region sales with the Servers cost from the quarterly review budget."}).json()
    print("\nSCENARIO 3:", body)
    assert not body["insufficient"]
    assert "100" in body["answer"] and "4200" in body["answer"].replace(",", "")
    _assert_triad_scored(body)
