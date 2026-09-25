# Task 3 — Hybrid retrieval with tenant pre-filter

**Purpose.** Dense (pgvector cosine) and sparse (Postgres FTS `ts_rank`) queries, both with a
mandatory `tenant_id = %s` predicate taken from the authenticated session object (never a request
field); fused by Reciprocal Rank Fusion; hits reassembled into full parent page/tab Markdown via FK.

**Inputs/outputs.** In: `Session(tenant_id)`, query text, k. Out: ranked parent sections with
scores and child provenance.

**Verification.** Unit: RRF math on fixed rankings. Integration (real Postgres): two tenants ingest
overlapping content → tenant A's query never returns tenant B's rows; exact-ID query found by
sparse leg.
