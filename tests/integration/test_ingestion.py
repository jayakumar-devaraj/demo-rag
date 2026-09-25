"""Rung-2 ingestion tests: dedup and stale-vector purge through the real pipeline and Postgres."""

import pytest

from src.db.connection import run
from src.db.repository import PageStatus
from src.ingestion.pipeline import ingest_file
from tests.conftest import FakeEmbedder, make_pdf, make_xlsx

pytestmark = pytest.mark.integration

PAGES = [("Ledger page one", [["ID", "Amount"], ["INV-001", "250"]]),
         ("Ledger page two", [["ID", "Amount"], ["INV-002", "900"]])]


async def _chunks(conn, tenant):
    cur = await conn.execute("SELECT content FROM chunks WHERE tenant_id = %s ORDER BY id", (tenant,))
    return [r[0] for r in await cur.fetchall()]


def test_renamed_pdf_with_one_modified_row_purges_only_that_page(conn, tenant):
    emb = FakeEmbedder()
    first = run(ingest_file(conn, emb, tenant, "ledger.pdf", make_pdf(PAGES)))
    assert [u.status for u in first.units] == [PageStatus.INSERTED, PageStatus.INSERTED]

    modified = [PAGES[0], ("Ledger page two", [["ID", "Amount"], ["INV-002", "999"]])]
    emb.calls.clear()
    second = run(ingest_file(conn, emb, tenant, "ledger_FINAL_v2.pdf", make_pdf(modified)))

    assert second.doc_key == first.doc_key  # rename detected by page-hash overlap
    assert [u.status for u in second.units] == [PageStatus.SKIPPED, PageStatus.REPLACED]
    assert emb.calls and all("page two" in c or "INV-002" in c for c in emb.calls)  # page 1 never re-embedded
    stored = " ".join(run(_chunks(conn, tenant)))
    assert "999" in stored and "900" not in stored  # no ghost chunks from the old row


def test_identical_reupload_embeds_nothing(conn, tenant):
    emb = FakeEmbedder()
    data = make_xlsx({"A": [["k", "v"], ["x", 1]], "B": [["k", "v"], ["y", 2]]})
    run(ingest_file(conn, emb, tenant, "book.xlsx", data))
    emb.calls.clear()
    again = run(ingest_file(conn, emb, tenant, "book.xlsx", data))
    assert all(u.status is PageStatus.SKIPPED for u in again.units)
    assert emb.calls == []


def test_dropped_tab_is_purged(conn, tenant):
    emb = FakeEmbedder()
    run(ingest_file(conn, emb, tenant, "book.xlsx", make_xlsx({"A": [["k"], ["keep"]], "B": [["k"], ["gone"]]})))
    result = run(ingest_file(conn, emb, tenant, "book.xlsx", make_xlsx({"A": [["k"], ["keep"]]})))
    assert result.purged_pages == 1
    assert "gone" not in " ".join(run(_chunks(conn, tenant)))
