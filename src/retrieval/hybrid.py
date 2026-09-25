"""Hybrid retrieval: dense (pgvector) + sparse (Postgres FTS) → RRF → parent-section assembly.

Every statement takes tenant_id from the authenticated Session and applies it as a WHERE
predicate before ranking, so no other tenant's rows are ever scored.
"""

import logging
from collections import defaultdict

import psycopg
from pgvector import Vector
from pydantic import BaseModel

from src.ingestion.pipeline import Embedder
from src.utils.auth import Session

log = logging.getLogger(__name__)

RRF_K = 60
LEG_LIMIT = 20

# Both legs run in one statement. The sparse leg ORs every query lexeme ('simple' config keeps
# identifiers such as INV-0072 intact) so natural-language filler words do not veto a match.
_HYBRID_SQL = """
WITH q AS (
    SELECT to_tsquery('simple', coalesce(nullif(array_to_string(array(
        SELECT quote_literal(l) FROM unnest(tsvector_to_array(to_tsvector('simple', %(text)s))) AS l
    ), ' | '), ''), '''''')) AS tsq
),
dense AS (
    SELECT id, 'dense' AS leg, row_number() OVER (ORDER BY embedding <=> %(vec)s::vector) AS rank
    FROM chunks WHERE tenant_id = %(tenant)s
    ORDER BY embedding <=> %(vec)s::vector LIMIT %(lim)s
),
sparse AS (
    SELECT c.id, 'sparse' AS leg, row_number() OVER (ORDER BY ts_rank(c.tsv, q.tsq) DESC, c.id) AS rank
    FROM chunks c, q WHERE c.tenant_id = %(tenant)s AND c.tsv @@ q.tsq
    ORDER BY ts_rank(c.tsv, q.tsq) DESC, c.id LIMIT %(lim)s
)
SELECT id, leg, rank FROM dense UNION ALL SELECT id, leg, rank FROM sparse
"""


class ParentSection(BaseModel):
    """A full parent page/tab reassembled from its matching child chunks."""

    page_id: int
    doc_key: str
    page_key: str
    source_name: str
    markdown: str
    score: float
    chunk_ids: list[int]


def rrf(rankings: list[list[int]], k: int = RRF_K) -> dict[int, float]:
    """Reciprocal Rank Fusion: score(d) = Σ 1 / (k + rank_d) over every ranking containing d."""
    scores: dict[int, float] = defaultdict(float)
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] += 1.0 / (k + rank)
    return dict(scores)


async def retrieve(
    conn: psycopg.AsyncConnection, embedder: Embedder, session: Session, query: str, top_k: int = 4
) -> list[ParentSection]:
    """Run both legs for the session's tenant, fuse with RRF, and return the top parent sections."""
    [vec] = await embedder.embed([query])
    cur = await conn.execute(_HYBRID_SQL, {"text": query, "vec": Vector(vec).to_text(),
                                           "tenant": session.tenant_id, "lim": LEG_LIMIT})
    legs: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for chunk_id, leg, rank in await cur.fetchall():
        legs[leg].append((rank, chunk_id))
    fused = rrf([[cid for _, cid in sorted(legs[leg])] for leg in ("dense", "sparse")])
    if not fused:
        return []
    cur = await conn.execute(
        "SELECT c.id, p.id, p.doc_key, p.page_key, p.source_name, p.markdown FROM chunks c"
        " JOIN page_registry p ON p.id = c.page_id"
        " WHERE c.id = ANY(%s) AND c.tenant_id = %s AND p.tenant_id = %s",
        (list(fused), session.tenant_id, session.tenant_id),
    )
    sections: dict[int, ParentSection] = {}
    for chunk_id, page_id, doc_key, page_key, source, markdown in await cur.fetchall():
        s = sections.setdefault(page_id, ParentSection(page_id=page_id, doc_key=doc_key, page_key=page_key,
                                                       source_name=source, markdown=markdown, score=0.0, chunk_ids=[]))
        s.score = max(s.score, fused[chunk_id])
        s.chunk_ids.append(chunk_id)
    ranked = sorted(sections.values(), key=lambda s: s.score, reverse=True)[:top_k]
    log.info("retrieved", extra={"tenant_id": session.tenant_id, "dense": len(legs["dense"]),
                                 "sparse": len(legs["sparse"]), "sections": len(ranked)})
    return ranked
