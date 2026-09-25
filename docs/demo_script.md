# Judge Demo

Two ways to run it:
- **Live run (primary):** Groq (`openai/gpt-oss-120b`) generates answers, local fastembed
  (`BAAI/bge-small-en-v1.5`, 384 dimensions) produces embeddings, and Ragas scores every answer.
  It takes about 80 seconds end to end.
- **Offline run:** the same scenarios with labeled test doubles in place of the models, in about 3
  seconds, for when there is no network or no key.

## Before you start

Start Postgres (README Quickstart), then put these in `.env`: `GROQ_API_KEY`,
`GROQ_CHAT_MODEL=openai/gpt-oss-120b` and `FASTEMBED_MODEL=BAAI/bge-small-en-v1.5`. OpenAI
(`OPENAI_API_KEY`, `OPENAI_CHAT_MODEL`) is optional; it's used only as the chat fallback. The first
run downloads the fastembed model (about 15 seconds), so run the command once beforehand to warm
the cache.

## Live run: about 80 seconds

```bash
uv run --env-file .env pytest -m live -s -p no:warnings tests/integration/test_live_llm.py
```

Expected result: `3 passed`. It was observed at 23:19–23:21 CDT on 2026-09-24, and took 79 seconds.
Each test prints the HTTP `/chat` response body. Talk through them as they appear:

**Scenario 1: multi-tab Excel keeps matrix coordinates** (`SCENARIO 1`)
- A two-tab workbook is ingested: `Ledger` with 80 invoices, and `Vendors`.
- The question is "What is the amount for invoice INV-0072?".
- The answer is `720`, citing `ledger.xlsx#tab:Ledger`. The row survived with its Excel row number,
  and the exact ID was found by the keyword leg of hybrid search.
- Observed triad: context relevance 1.0, groundedness 1.0, answer relevance 0.44 (Ragas scores one-word answers low), numeric groundedness 1.0.

**Scenario 2: renamed PDF with one modified row** (`SCENARIO 2 ingest` / `SCENARIO 2 chat`)
- `ledger.pdf` is ingested, then re-uploaded as `ledger_FINAL_v2.pdf` with one changed amount.
- Ingest statuses go `['inserted', 'inserted'] -> ['skipped', 'replaced']`. The unchanged page cost
  nothing (no chunking, no embedding). The changed page's old chunks were purged in the same
  transaction that inserted the new ones.
- The answer is `999`, citing `ledger.pdf#page:2`. The stale value `900` is gone.
- Observed triad: context relevance 1.0, groundedness 1.0, answer relevance 0.54.

**Scenario 3: query across both document types** (`SCENARIO 3`)
- A Word review (budget table) and an Excel sales sheet are ingested.
- The question is "Compare North region sales with the Servers cost from the quarterly review budget."
- The answer quotes `100` and `4200` and cites both `s.xlsx#tab:Q1` and `r.docx#page:1`.
- Observed triad: context relevance 1.0, groundedness 1.0, answer relevance 0.94, numeric
  groundedness 1.0.
- `attempts` shows how many retrieval passes the self-correction loop used. In the observed run,
  hybrid retrieval found both documents on the first pass (`attempts: 1`). The re-query branch is
  shown deterministically in the offline run below.

**What to point out in the logs** (`uv run --env-file .env python -m src.main` in another terminal):
- Every line is JSON with a `request_id`.
- `node` lines give the timing of each graph step.
- `chat_answered` carries the tenant, the number of passes and the full triad.

## Offline run: about 3 seconds, no key needed

```bash
uv run --env-file .env pytest -v -p no:warnings tests/unit/test_workers.py::test_xlsx_each_tab_is_a_unit_with_coordinates tests/integration/test_retrieval.py::test_exact_identifier_found_and_parent_table_reassembled tests/integration/test_ingestion.py::test_renamed_pdf_with_one_modified_row_purges_only_that_page tests/integration/test_api_e2e.py::test_ingest_then_self_correcting_chat_across_docx_and_xlsx tests/integration/test_retrieval.py::test_query_never_returns_another_tenants_rows
```

Expected result: `5 passed`. The LLM and embedder are labeled test doubles (`tests/conftest.py`);
Postgres, the parsers and the graph are real. This run also shows two things the live run doesn't
force:
- **A scripted re-query pass:** `queries_run = [question, "Servers"]`, `attempts = 2`.
- **Tenant isolation over HTTP:** the second tenant gets the insufficient-data fallback for the
  first tenant's question.
