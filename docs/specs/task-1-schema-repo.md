# Task 1 — Skeleton, schema, transactional repo

**Purpose.** Package skeleton per the master-plan layout; Postgres+pgvector schema (`page_registry`,
`chunks` with `tenant_id`, FK `chunks.page_id → page_registry.id ON DELETE CASCADE`); a repository
that replaces a page's chunks (purge + insert) inside one ACID transaction; README Tech Stack section
read from `pyproject.toml`/`uv.lock`.

**Inputs/outputs.** In: `(tenant_id, doc_id, page_key, sha256, markdown, chunks[])`. Out: page
status `skipped | inserted | replaced`.

**Verification (Rule 6).** Unit: status decision logic. Integration (rung 2, real Postgres):
same hash → `skipped`, no chunk writes; changed hash → old chunks gone, new present; failure
mid-transaction → old chunks still present (rollback).
