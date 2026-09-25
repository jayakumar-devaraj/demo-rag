"""Transactional page/chunk repository: SHA-256 gate + cascading purge + upsert in one ACID unit."""

import logging
from enum import StrEnum

import psycopg
from pgvector import Vector
from pydantic import BaseModel, Field

from src.db.connection import EMBEDDING_DIM

log = logging.getLogger(__name__)


class PageStatus(StrEnum):
    """Outcome of writing one page/tab."""

    SKIPPED = "skipped"
    INSERTED = "inserted"
    REPLACED = "replaced"


class ChunkIn(BaseModel):
    """One child chunk ready to persist."""

    content: str = Field(min_length=1)
    embedding: list[float] = Field(min_length=EMBEDDING_DIM, max_length=EMBEDDING_DIM)


class PageIn(BaseModel):
    """One normalized page or spreadsheet tab, identified per tenant/document/page."""

    tenant_id: str = Field(min_length=1)
    doc_key: str = Field(min_length=1)
    page_key: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_name: str
    markdown: str


async def stored_hash(conn: psycopg.AsyncConnection, tenant_id: str, doc_key: str, page_key: str) -> str | None:
    """Return the registered SHA-256 for a page, or None if the page is unknown."""
    cur = await conn.execute(
        "SELECT sha256 FROM page_registry WHERE tenant_id = %s AND doc_key = %s AND page_key = %s",
        (tenant_id, doc_key, page_key),
    )
    row = await cur.fetchone()
    return row[0] if row else None


async def write_page(conn: psycopg.AsyncConnection, page: PageIn, chunks: list[ChunkIn]) -> PageStatus:
    """Persist a page and its chunks atomically.

    Same hash as registered → SKIPPED (only source_name is refreshed, e.g. after a rename).
    Changed hash → the old page row is deleted (FK cascade purges its chunks) and the new page and
    chunks are inserted, all inside one transaction, so a failure leaves the old state intact.
    """
    async with conn.transaction():
        cur = await conn.execute(
            "SELECT id, sha256 FROM page_registry WHERE tenant_id = %s AND doc_key = %s AND page_key = %s FOR UPDATE",
            (page.tenant_id, page.doc_key, page.page_key),
        )
        existing = await cur.fetchone()
        if existing and existing[1] == page.sha256:
            await conn.execute(
                "UPDATE page_registry SET source_name = %s WHERE id = %s", (page.source_name, existing[0])
            )
            status = PageStatus.SKIPPED
        else:
            if existing:
                await conn.execute("DELETE FROM page_registry WHERE id = %s", (existing[0],))
            cur = await conn.execute(
                "INSERT INTO page_registry (tenant_id, doc_key, page_key, sha256, source_name, markdown)"
                " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (page.tenant_id, page.doc_key, page.page_key, page.sha256, page.source_name, page.markdown),
            )
            page_id = (await cur.fetchone())[0]
            async with conn.cursor() as c:
                await c.executemany(
                    "INSERT INTO chunks (tenant_id, page_id, ord, content, embedding)"
                    " VALUES (%s, %s, %s, %s, %s::vector)",
                    [
                        (page.tenant_id, page_id, i, ch.content, Vector(ch.embedding).to_text())
                        for i, ch in enumerate(chunks)
                    ],
                )
            status = PageStatus.REPLACED if existing else PageStatus.INSERTED
    log.info(
        "page_written",
        extra={"tenant_id": page.tenant_id, "doc_key": page.doc_key, "page_key": page.page_key,
               "status": status.value, "chunks": 0 if status is PageStatus.SKIPPED else len(chunks)},
    )
    return status


async def find_doc_by_hashes(conn: psycopg.AsyncConnection, tenant_id: str, hashes: list[str]) -> str | None:
    """Return the tenant's document sharing the most page hashes with an upload (rename detection)."""
    cur = await conn.execute(
        "SELECT doc_key FROM page_registry WHERE tenant_id = %s AND sha256 = ANY(%s)"
        " GROUP BY doc_key ORDER BY count(*) DESC, max(updated_at) DESC LIMIT 1",
        (tenant_id, hashes),
    )
    row = await cur.fetchone()
    return row[0] if row else None


async def delete_missing_pages(
    conn: psycopg.AsyncConnection, tenant_id: str, doc_key: str, keep_page_keys: list[str]
) -> int:
    """Delete a document's pages absent from its latest version (FK cascade purges their chunks)."""
    async with conn.transaction():
        cur = await conn.execute(
            "DELETE FROM page_registry WHERE tenant_id = %s AND doc_key = %s AND NOT (page_key = ANY(%s))",
            (tenant_id, doc_key, keep_page_keys),
        )
    return cur.rowcount
