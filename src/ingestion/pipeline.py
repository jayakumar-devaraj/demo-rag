"""Ingestion pipeline: worker → SHA-256 gate → chunk → embed → transactional write."""

import asyncio
import logging
from typing import Protocol

import psycopg
from pydantic import BaseModel

from src.db.repository import (
    ChunkIn,
    PageIn,
    PageStatus,
    delete_missing_pages,
    find_doc_by_hashes,
    stored_hash,
    write_page,
)
from src.ingestion.chunking import chunk, normalize, signature
from src.ingestion.workers import worker_for

log = logging.getLogger(__name__)


class Embedder(Protocol):
    """Async text-embedding provider."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text."""
        ...


class UnitResult(BaseModel):
    """Per page/tab outcome of an ingest."""

    page_key: str
    sha256: str
    status: PageStatus
    chunks: int


class IngestResult(BaseModel):
    """Outcome of ingesting one file."""

    doc_key: str
    units: list[UnitResult]
    purged_pages: int


async def ingest_file(
    conn: psycopg.AsyncConnection,
    embedder: Embedder,
    tenant_id: str,
    filename: str,
    data: bytes,
    doc_key: str | None = None,
) -> IngestResult:
    """Ingest one file for a tenant.

    Without an explicit doc_key, an upload whose page hashes overlap an existing document of the
    same tenant is treated as a new version of it (so a renamed file reuses unchanged pages);
    otherwise the filename is the document key. Pages whose hash is already registered skip
    chunking and embedding entirely.
    """
    units = await asyncio.to_thread(worker_for(filename).extract, data)  # CPU-bound: off the loop
    hashes = [signature(u.markdown) for u in units]
    doc_key = doc_key or await find_doc_by_hashes(conn, tenant_id, hashes) or filename
    results = []
    for unit, sha in zip(units, hashes, strict=True):
        page = PageIn(tenant_id=tenant_id, doc_key=doc_key, page_key=unit.page_key, sha256=sha,
                      source_name=filename, markdown=normalize(unit.markdown))
        chunks: list[ChunkIn] = []
        if await stored_hash(conn, tenant_id, doc_key, unit.page_key) != sha:
            texts = chunk(unit.markdown)
            vectors = await embedder.embed(texts) if texts else []
            chunks = [ChunkIn(content=t, embedding=v) for t, v in zip(texts, vectors, strict=True)]
        status = await write_page(conn, page, chunks)
        results.append(UnitResult(page_key=unit.page_key, sha256=sha, status=status,
                                  chunks=0 if status is PageStatus.SKIPPED else len(chunks)))
    purged = await delete_missing_pages(conn, tenant_id, doc_key, [u.page_key for u in units])
    log.info("file_ingested", extra={"tenant_id": tenant_id, "doc_key": doc_key, "source": filename,
                                     "units": len(units), "purged_pages": purged})
    return IngestResult(doc_key=doc_key, units=results, purged_pages=purged)
