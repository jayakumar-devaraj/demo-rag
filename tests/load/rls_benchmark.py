"""Benchmark the hybrid retrieval statement with row-level security off and on (ADR 0002, "check before relying on RLS").

Usage: `uv run --env-file .env python tests/load/rls_benchmark.py [--tenants 20] [--per-tenant 1000] [--runs 200]`
Needs the compose Postgres from docker-compose.yml (DATABASE_URL is the owner/superuser). It creates and drops a
scratch database and prints a Markdown table. Vectors are random, so this measures plans and latency, not relevance.
"""

import argparse
import asyncio
import random
import statistics
import sys
import time

import psycopg
from pgvector import Vector

from src.db.connection import database_url
from src.retrieval.hybrid import _HYBRID_SQL
from tests.migration_support import unique, upgrade_head, with_credentials, with_database

WORDS = [f"w{i}" for i in range(300)]
DIM = 384


def _vec(rng: random.Random) -> str:
    return Vector([rng.gauss(0, 1) for _ in range(DIM)]).to_text()


async def _load(url: str, tenants: int, per_tenant: int, rng: random.Random) -> None:
    async with await psycopg.AsyncConnection.connect(url, autocommit=True) as c:
        await c.execute("DROP INDEX chunks_embedding")  # rebuilt after the load, which is much faster
        for t in range(tenants):
            cur = await c.execute(
                "INSERT INTO page_registry (tenant_id, doc_key, page_key, sha256, source_name, markdown) "
                "VALUES (%s, 'd', 'p', %s, 's', 'm') RETURNING id", (f"t{t}", f"{t:064x}"))
            page_id = (await cur.fetchone())[0]
            async with c.cursor().copy("COPY chunks (tenant_id, page_id, ord, content, embedding) FROM STDIN") as cp:
                for i in range(per_tenant):
                    await cp.write_row((f"t{t}", page_id, i, " ".join(rng.sample(WORDS, 8)), _vec(rng)))
        await c.execute("CREATE INDEX chunks_embedding ON chunks USING hnsw (embedding vector_cosine_ops)")
        await c.execute("ANALYZE")


async def _time(url: str, sql: str, tenant: str | None, runs: int, rng: random.Random) -> tuple[list[float], int]:
    """Latencies (ms) over `runs` queries for tenants t0..; second value is the mean rows returned."""
    ms, rows = [], 0
    async with await psycopg.AsyncConnection.connect(url, autocommit=True) as c:
        for i in range(runs):
            t = tenant if tenant is not None else f"t{i % 5}"
            await c.execute("SELECT set_config('app.tenant_id', %s, false)", (t,))
            params = {"text": " ".join(rng.sample(WORDS, 3)), "vec": _vec(rng), "tenant": t, "lim": 20}
            start = time.perf_counter()
            rows += len(await (await c.execute(sql, params)).fetchall())
            ms.append((time.perf_counter() - start) * 1000)
    return ms, rows // runs


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--tenants", type=int, default=20)
    p.add_argument("--per-tenant", type=int, default=1000)
    p.add_argument("--runs", type=int, default=200)
    a = p.parse_args()
    rng = random.Random(7)
    admin = await psycopg.AsyncConnection.connect(database_url(), autocommit=True)
    role, password = unique("bench"), unique("pw")
    await admin.execute(f"CREATE ROLE {role} LOGIN PASSWORD '{password}' NOSUPERUSER NOCREATEDB NOCREATEROLE")
    try:
        name = unique("bench")
        await admin.execute(f'CREATE DATABASE "{name}"')
        try:
            owner_url = with_database(database_url(), name)
            await asyncio.to_thread(upgrade_head, owner_url, role)
            app_url = with_credentials(owner_url, role, password)
            await _load(owner_url, a.tenants, a.per_tenant, rng)
            no_predicate = _HYBRID_SQL.replace("WHERE tenant_id = %(tenant)s", "").replace(
                "c.tenant_id = %(tenant)s AND ", "")
            cases = [("owner (RLS bypassed), tenant predicate", owner_url, _HYBRID_SQL),
                     ("app role, RLS + tenant predicate (production)", app_url, _HYBRID_SQL),
                     ("app role, RLS only (predicate removed)", app_url, no_predicate)]
            print(f"{a.tenants * a.per_tenant} chunks, {a.tenants} tenants, {a.runs} queries per case\n")
            print("| case | p50 ms | p95 ms | mean rows returned |\n|---|---|---|---|")
            for label, url, sql in cases:
                ms, rows = await _time(url, sql, None, a.runs, rng)
                q = statistics.quantiles(ms, n=20)
                print(f"| {label} | {statistics.median(ms):.1f} | {q[18]:.1f} | {rows} |")
            async with await psycopg.AsyncConnection.connect(app_url, autocommit=True) as c:
                await c.execute("SELECT set_config('app.tenant_id', 't1', false)")
                params = {"text": "w1 w2", "vec": _vec(rng), "tenant": "t1", "lim": 20}
                plan = await (await c.execute("EXPLAIN " + _HYBRID_SQL, params)).fetchall()
            print("\nPlan, app role with RLS (first lines):")
            print("\n".join(r[0][:120] for r in plan[:14]))
        finally:
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.execute(f"DROP ROLE IF EXISTS {role}")
        await admin.close()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
