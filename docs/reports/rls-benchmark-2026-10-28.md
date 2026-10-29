# Row-level security and pgvector: benchmark, 2026-10-28

ADR 0002 requires checking that the policies do not change the query plan or slow retrieval before
relying on them. Command, run twice on the same machine against the compose Postgres
(`pgvector/pgvector:pg16`) with a scratch database created and dropped by the script:

```bash
PYTHONPATH=. uv run --env-file .env python tests/load/rls_benchmark.py --tenants 20 --per-tenant 1000 --runs 200
```

Data: 20,000 chunks (20 tenants x 1,000), random 384-dimension vectors, HNSW index built after load.
It measures the hybrid retrieval statement (`src/retrieval/hybrid.py`), 200 queries per case, one
connection per case. Vectors are random, so this shows plans and latency, not retrieval quality.

| case | run 1 p50 / p95 ms | run 2 p50 / p95 ms | mean rows returned |
|---|---|---|---|
| owner role (RLS bypassed), tenant predicate | 6.9 / 9.4 | 4.1 / 6.4 | 22 |
| app role, RLS + tenant predicate (production) | 4.3 / 6.2 | 4.4 / 6.8 | 22 |
| app role, RLS only (predicate removed) | 4.4 / 7.2 | 4.6 / 8.3 | 22 |

## What this shows
- **No measurable overhead at this scale.** The first case swings from 6.9 ms to 4.1 ms p50 between
  runs, a larger change than any difference between cases, so the spread is run-to-run noise (that case
  runs first, on a cold cache). Do not read the app role being faster in run 1 as a benefit of RLS.
- **Same results.** All three cases returned the same mean number of rows (22), so the policy did not
  change how many candidates the filtered index scan produced here.
- **Same plan.** The policy appears as a one-time filter
  (`current_setting('app.tenant_id', true) = 't1'`), evaluated once rather than per row; the dense leg
  still uses `Index Scan using chunks_embedding` with `Filter: (tenant_id = 't1')`.

## Not covered
- Only 20,000 chunks. Larger corpora, and many tenants with few rows each, can behave differently.
- HNSW filters after the index scan, so a small tenant in a large table can get fewer than the
  requested candidates. That is true with or without RLS (the predicate was already in the SQL) and is
  not measured here.
- Single connection, no concurrency, no network.
