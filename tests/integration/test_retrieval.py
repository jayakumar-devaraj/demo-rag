"""Rung-2 retrieval tests: tenant isolation, exact-ID sparse recall, parent assembly (real Postgres)."""

import uuid

import pytest

from src.db.connection import run
from src.ingestion.pipeline import ingest_file
from src.retrieval.hybrid import retrieve
from src.utils.auth import Session
from tests.conftest import FakeEmbedder, make_xlsx

pytestmark = pytest.mark.integration


def test_query_never_returns_another_tenants_rows(conn):
    a, b = Session(tenant_id=f"a-{uuid.uuid4().hex[:8]}", subject="test"), Session(tenant_id=f"b-{uuid.uuid4().hex[:8]}", subject="test")
    emb = FakeEmbedder()
    shared = [["Serial", "Owner"], ["SN-4471", "finance"]]
    run(ingest_file(conn, emb, a.tenant_id, "assets.xlsx", make_xlsx({"S": shared + [["SN-1", "tenant-a-only"]]})))
    run(ingest_file(conn, emb, b.tenant_id, "assets.xlsx", make_xlsx({"S": shared + [["SN-2", "tenant-b-only"]]})))

    for session, own, other in [(a, "tenant-a-only", "tenant-b-only"), (b, "tenant-b-only", "tenant-a-only")]:
        sections = run(retrieve(conn, emb, session, "SN-4471 owner"))
        text = " ".join(s.markdown for s in sections)
        assert own in text and other not in text


def test_exact_identifier_found_and_parent_table_reassembled(conn, tenant):
    emb = FakeEmbedder()
    rows = [["Invoice", "Amount"]] + [[f"INV-{i:04d}", i * 10] for i in range(80)]  # > 1 chunk per tab
    run(ingest_file(conn, emb, tenant, "ledger.xlsx", make_xlsx({"Ledger": rows, "Notes": [["n"], ["misc"]]})))
    sections = run(retrieve(conn, emb, Session(tenant_id=tenant, subject="test"), "amount for INV-0072"))
    top = sections[0]
    assert top.page_key == "tab:Ledger"
    assert "| 74 | INV-0072 | 720 |" in top.markdown  # Excel row 74 carries the matched row
    assert "INV-0000" in top.markdown  # whole parent tab, not just the matched chunk
