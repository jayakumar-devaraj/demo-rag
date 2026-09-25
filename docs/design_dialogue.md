# Design Dialogue — Step A (Stack Bundles & Architecture)

Live transcript of Phase 1 / Step A, per `docs/prompts/on_site_coding_challenge.md`. Append-only
after Gate A locks.

## Architecture (shared across all three bundles)

```
Upload (PDF / XLSX / DOCX / TXT, + tenant/session id)
   │
   ▼
[1] Polymorphic Ingestion Workers  (PDF/XLSX/DOCX/TXT → per-page/per-tab Markdown, tenant-tagged)
   │
   ▼
[2] SHA-256 Gate  ──────────────►  page_registry (relational: hash, tenant_id, doc_id, page/tab id)
   │  hash matches → skip embedding entirely
   │  hash changed → [3] cascading DELETE of stale child chunks, same transaction as the new upsert
   ▼
[5] Semantic, structure-aware Chunking (table/paragraph-boundary aware, tenant-tagged)
   │
   ▼
Embeddings + upsert → vector store + sparse/keyword index (tenant-namespaced)

User Query (+ tenant/session id)
   │
   ▼
[4] Access-control pre-filter (tenant_id bound to the authenticated session — mandatory, not optional)
   │
   ▼
[6] Hybrid Retrieval: dense (vector) ∥ sparse (keyword/exact) → Reciprocal Rank Fusion
   │
   ▼
Parent-section assembly (child chunk → parent page/tab via FK, full table/paragraph reassembled)
   │
   ▼
[7] Agentic Orchestrator (stateful loop): sufficiency check → secondary re-query on gaps →
    control gate: "insufficient data" fallback if still short after correction loop
   │
   ▼
Synthesis (LLM, schema-validated structured output, prompt-injection-guarded)
   │
   ▼
[8] RAG-Triad Eval (context relevance, groundedness, answer relevance) — logged to trace store
   │
   ▼
Chat/API response
```

Bracketed numbers map to the assignment's Core Requirements 1–8. Bundle-specific shape changes are
noted under each bundle below.

**Held fixed across all three bundles** (problem statement names none of these; `CLAUDE.md` §9 does):
async-first I/O for every network/LLM call; schema-validated structured LLM output (Pydantic v2 in
the Python bundles, Zod in the TypeScript bundle); explicit 4xx-vs-5xx distinction at the API
boundary; a named prompt-injection defense (this system ingests uploaded documents — untrusted
content — so guardrails and observability are triggered per the trigger rule); trace-level
observability; orchestration framework from the LangChain/LangGraph family (Bundle 2 is the one
deviation, justified inline below). Access control is triggered because the assignment explicitly
names tenancy — every bundle binds tenant_id to the authenticated session as a mandatory retrieval
pre-filter, not a client-supplied, unauthenticated key. Human-approval gate is N/A in all three: the
only mutating actions are deterministic, hash-gated, user-initiated pipeline steps (upsert/delete
of that user's own uploaded content) — no autonomous action with external real-world consequences.

## Bundle 1 — Python / LangGraph / Postgres+pgvector (single store) / Ragas — **Recommended**

Stack: Python 3.12; LangGraph 1.2.12 + langchain-core 1.6.5; FastAPI 0.141.1; Postgres + pgvector
0.5.0, psycopg 3.3.6; Pydantic 2.13.5; PyMuPDF 1.28.2, openpyxl 3.1.5, python-docx 1.2.0; Ragas
0.4.3; Langfuse 4.15.6.
Protected concerns: guardrails/observability triggered — Pydantic-validated I/O, an explicit
injection-defense system prompt + input sanitizer, Langfuse tracing. Access control triggered —
tenant_id bound to session, enforced as a mandatory query pre-filter. Human-approval gate: N/A
(see above).
Trade-off: reliability — one transactional store makes the cascading-delete requirement a real ACID
transaction, not a best-effort sync across two systems.
Failure mode / ceiling: pgvector's HNSW/IVFFlat recall and latency lag a dedicated vector DB past
roughly 1M vectors — irrelevant at this demo's scale.
API confidence: LangGraph's 1.x checkpointing/state API — `VERIFY:` at implementation.
Version policy: latest stable (no LTS channel for these libraries).

## Bundle 2 — Python / LlamaIndex / Qdrant + SQLite (split stores) / DeepEval

Stack: Python 3.12; llama-index-core 0.14.25; Qdrant client 1.19.1 (vector store); SQLite (stdlib,
relational page_registry); Pydantic 2.13.5; PyMuPDF 1.28.2, openpyxl 3.1.5, python-docx 1.2.0;
DeepEval 4.2.6; FastAPI 0.141.1.
Orchestration deviation: LlamaIndex's RouterQueryEngine/SubQuestionQueryEngine replace
LangGraph here — chosen for built-in document loaders and native hybrid search that cut custom
code under this time budget; the trade-off it buys is spelled out below.
Protected concerns: same triggers as Bundle 1 — guardrails/observability via DeepEval + structured
logging (no first-class LangSmith/Langfuse integration, more manual wiring). Access control via a
Qdrant payload filter + SQLite tenant column, both enforced pre-query. Human-approval gate: N/A,
same reasoning as above.
Trade-off: build speed — less custom retrieval/loader code — against reliability: cascading delete
now spans two independent stores, so atomicity is only best-effort (delete-then-insert), not a
single transaction.
Failure mode / ceiling: a crash between the SQLite commit and the Qdrant delete leaves orphan
vectors — the exact defect requirement 3 asks to eliminate.
API confidence: LlamaIndex's router/query-engine API surface churns between minor versions —
`VERIFY:`.
Version policy: latest stable.

## Bundle 3 — TypeScript / LangChain.js / Postgres+pgvector / custom eval harness

Stack: Node + TypeScript 7.0.2; langchain 1.5.12; pg 8.23.0 + pgvector (npm) 0.3.0; zod 4.6.5;
express 5.2.1; pdf-parse 2.4.5, xlsx (SheetJS) 0.18.5, mammoth 1.12.3; hand-rolled RAG-triad scorer
(no mature JS Ragas/DeepEval equivalent exists).
Protected concerns: same triggers as Bundle 1 — guardrails via Zod-validated I/O + injection-defense
middleware; observability via OpenTelemetry (JS Langfuse/LangSmith parity is thinner than Python's).
Access control: same pattern as Bundle 1, in Postgres. Human-approval gate: N/A, same reasoning.
Trade-off: end-to-end type safety and a single async-native runtime, against weaker document-parsing
fidelity — pdf-parse/SheetJS/mammoth extract less layout/table structure than PyMuPDF/openpyxl,
which directly threatens this assignment's central ask (preserving row/column matrix alignment).
Failure mode / ceiling: a hand-rolled RAG-triad scorer, built under this time budget, risks a
shallow or incomplete requirement 8 relative to Ragas/DeepEval's tested implementations.
API confidence: LangChain.js's LCEL/agent surface is less battle-tested than Python LangGraph —
`VERIFY:`.
Version policy: latest stable.

## E3 — per-library confirmation

- Store: single Postgres+pgvector (Bundles 1, 3) vs. split Qdrant+SQLite (Bundle 2) — transactional
  integrity vs. build speed?
- Orchestration: LangGraph (Python, stateful graph, Bundle 1) vs. LlamaIndex query engines (faster
  to wire, less control, Bundle 2) vs. LangChain.js (TypeScript, Bundle 3)?
- Eval harness: Ragas (Bundle 1) vs. DeepEval (Bundle 2) vs. hand-rolled (Bundle 3) for the
  RAG-triad requirement?
- Observability: Langfuse (Bundles 1) vs. OpenTelemetry/manual structured logging (Bundles 2, 3)?
- Language: Python (Bundles 1, 2) vs. TypeScript (Bundle 3) — given table-parsing fidelity is
  central to this assignment's core ask?

## Gate A — lock

User's reply, verbatim: "Bundle 1"

Names exactly one bundle, changes nothing else — locks per the Gate A rule (a reply naming exactly
one bundle with no other changes locks it without a further confirmation round).

**Locked: Bundle 1 — Python 3.12 / LangGraph 1.2.12 + langchain-core 1.6.5 / FastAPI 0.141.1 /
Postgres + pgvector 0.5.0 (psycopg 3.3.6) / Pydantic 2.13.5 / PyMuPDF 1.28.2, openpyxl 3.1.5,
python-docx 1.2.0 / Ragas 0.4.3 / Langfuse 4.15.6.**
