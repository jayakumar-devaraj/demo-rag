"""Rung-2 tests for the SHA-256 gate and transactional cascading purge against real Postgres."""

import hashlib

import psycopg
import pytest

from src.db.connection import EMBEDDING_DIM, run
from src.db.repository import ChunkIn, PageIn, PageStatus, stored_hash, write_page

pytestmark = pytest.mark.integration


def _page(tenant: str, markdown: str, source: str = "report.pdf") -> PageIn:
    return PageIn(tenant_id=tenant, doc_key="doc-1", page_key="page:1",
                  sha256=hashlib.sha256(markdown.encode()).hexdigest(), source_name=source, markdown=markdown)


def _chunks(*texts: str) -> list[ChunkIn]:
    return [ChunkIn(content=t, embedding=[0.1] * EMBEDDING_DIM) for t in texts]


async def _chunk_texts(conn: psycopg.AsyncConnection, tenant: str) -> list[str]:
    cur = await conn.execute("SELECT content FROM chunks WHERE tenant_id = %s ORDER BY ord", (tenant,))
    return [r[0] for r in await cur.fetchall()]


def test_first_write_inserts(conn, tenant):
    page = _page(tenant, "| a | b |")
    assert run(write_page(conn, page, _chunks("row1", "row2"))) is PageStatus.INSERTED
    assert run(stored_hash(conn, tenant, "doc-1", "page:1")) == page.sha256
    assert run(_chunk_texts(conn, tenant)) == ["row1", "row2"]


def test_same_hash_is_skipped_even_after_rename(conn, tenant):
    run(write_page(conn, _page(tenant, "| a | b |"), _chunks("row1")))
    status = run(write_page(conn, _page(tenant, "| a | b |", source="renamed.pdf"), _chunks("SHOULD-NOT-WRITE")))
    assert status is PageStatus.SKIPPED
    assert run(_chunk_texts(conn, tenant)) == ["row1"]


def test_changed_hash_purges_stale_chunks(conn, tenant):
    run(write_page(conn, _page(tenant, "v1"), _chunks("old-a", "old-b")))
    status = run(write_page(conn, _page(tenant, "v2"), _chunks("new-a")))
    assert status is PageStatus.REPLACED
    assert run(_chunk_texts(conn, tenant)) == ["new-a"]


def test_failure_mid_transaction_keeps_old_state(conn, tenant):
    old = _page(tenant, "v1")
    run(write_page(conn, old, _chunks("old-a")))
    # NUL bytes are rejected by Postgres text columns: the chunk insert fails after the purge ran.
    with pytest.raises(psycopg.DataError):
        run(write_page(conn, _page(tenant, "v2"), _chunks("bad\x00chunk")))
    assert run(stored_hash(conn, tenant, "doc-1", "page:1")) == old.sha256
    assert run(_chunk_texts(conn, tenant)) == ["old-a"]
