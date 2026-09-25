# 60-Second Judge Demo

No model credentials were available during the build, so every scenario runs through the automated
tests. They use real Postgres and pgvector, the real parsers and the real graph. LLM and embedding
calls are **labeled test doubles** (`tests/conftest.py`). Setup is in the README Quickstart:
Postgres must be running, and `.env` must exist.

**0:00–0:10 — Start.** Run the five scenario tests:

```bash
uv run --env-file .env pytest -v -p no:warnings tests/unit/test_workers.py::test_xlsx_each_tab_is_a_unit_with_coordinates tests/integration/test_retrieval.py::test_exact_identifier_found_and_parent_table_reassembled tests/integration/test_ingestion.py::test_renamed_pdf_with_one_modified_row_purges_only_that_page tests/integration/test_api_e2e.py::test_ingest_then_self_correcting_chat_across_docx_and_xlsx tests/integration/test_retrieval.py::test_query_never_returns_another_tenants_rows
```

Expected result: `5 passed`. It was observed at 22:29 CDT on 2026-09-24.

**0:10–0:20 — Scenario 1, multi-tab Excel keeps matrix coordinates.** Each tab becomes its own unit.
Headers are prefixed with their Excel column letter (`A:Region`), and every row keeps its Excel row
number (`| 3 | South | 80 |`). A search for `INV-0072` returns the whole `Ledger` tab, including
`| 74 | INV-0072 | 720 |`, and not only the matched chunk.

**0:20–0:35 — Scenario 2, renamed PDF with one modified row.** The file is re-uploaded as
`ledger_FINAL_v2.pdf` with one changed cell. Rename detection by page-hash overlap maps it to the
original document. Page 1 is `SKIPPED` and never re-embedded (the embedder double records no page-1
calls). Page 2 is `REPLACED`: its old chunks are deleted by the FK cascade in the same transaction,
and the old value `900` no longer exists anywhere in `chunks`.

**0:35–0:50 — Scenario 3, ambiguous query needing a self-correcting lookup across DOCX and XLSX.**
The question "North sales vs budget" is answered over HTTP. The first evaluation reports `Servers` as
missing, so the graph runs a second retrieval pass, appends that context and synthesizes. The
response shows `queries_run = [question, "Servers"]`, `attempts = 2` and numeric groundedness `1.0`.

**0:50–1:00 — Tenant isolation.** Two tenants hold overlapping data. Each tenant's query returns only
its own rows, and over HTTP the second tenant gets the insufficient-data fallback for the first
tenant's question.

**Optional live run (needs keys):** set `OPENAI_*` in `.env`, start `uv run --env-file .env python -m src.main`,
then `POST /ingest` a file and `POST /chat` a question with `Authorization: Bearer <token>`. This path
is **UNVERIFIED** (see `docs/known_limitations.md`).
