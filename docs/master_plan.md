# Master Plan

Single source of truth for the build governed by `docs/prompts/on_site_coding_challenge.md`.
Any agreed cut updates this file in the same commit as the cut.

- Start 21:34 CDT · Deadline 23:34 CDT (2 h) · Reserve 30 min · Phase 3 floor 20 min.

## Architecture

See the shared data-flow diagram in [`design_dialogue.md`](design_dialogue.md#architecture-shared-across-all-three-bundles)
(Bundle 1 does not change its shape). Single process, single repo; one Postgres+pgvector store holds
`page_registry`, `chunks` (dense vectors + FTS `tsvector`), so hash-gate, cascading purge and upsert
share one ACID transaction.

## Locked stack (Bundle 1, Gate A)

Python 3.13.13 (managed by `uv`) · LangGraph 1.2.12 + langchain-core 1.6.5 · FastAPI 0.141.1 ·
Postgres + pgvector 0.5.0, psycopg 3.3.6 · Pydantic 2.13.5 · PyMuPDF 1.28.2, openpyxl 3.1.5,
python-docx 1.2.0 · Ragas 0.4.3 · Langfuse 4.15.6.

Python version note: `design_dialogue.md` records 3.12 at lock; the build pins 3.13.13 (installed
via `uv`, chosen over 3.14.5 for C-extension wheel availability) — see the appended entry there.

## Layout (binding for Rule 10)

```
src/
  routers/        FastAPI routes (chat, ingest)           existing stub dir
  db/             schema, connection, transactional repo   new
  ingestion/      polymorphic workers, hashing, chunking   new
  retrieval/      dense + sparse + RRF + parent assembly   new
  agents/         synthesis + sufficiency evaluator        existing stub dir
  orchestration/  LangGraph state + graph                  existing stub dir
  eval/           RAG-triad scoring                        new
  tools/          retrieval tool wrappers for the graph    existing stub dir
  utils/          logging, tracing, config                 existing stub dir
  main.py         single runnable entrypoint (FastAPI app)
tests/unit/ , tests/integration/   (integration = real Postgres, marked `integration`)
```

## Roadmap

| # | Task | Est. | Covers |
|---|------|------|---|
| 0 | Persist plan: `docs/master_plan.md`, `docs/specs/task-N` files, `docs/AI_ORCHESTRATION.md`, `docs/known_limitations.md` | 5 min | — |
| 1 | Skeleton + Postgres/pgvector schema (`page_registry`, `chunks`, `tenant_id` column) + transactional repo (upsert + cascading delete in one ACID transaction) + README Tech Stack section (read from manifest) | 15 min | Req 2, 3 |
| 2 | Polymorphic ingestion: PDF/XLSX/DOCX/TXT → Markdown workers, SHA-256 gate per page/tab, structure-aware chunking, wired to Task 1's dedup/purge | 22 min | Req 1, 2, 3, 5 |
| 3 | Hybrid retrieval: dense (pgvector) + sparse (Postgres FTS) → RRF, mandatory `tenant_id` pre-filter bound to authenticated session, parent-section reassembly via FK | 15 min | Req 4, 6 |
| 4 | LangGraph self-correction loop (sufficiency → re-query → control-gate fallback) + structured, injection-guarded synthesis (Pydantic) + Ragas RAG-triad logging + tracing (Langfuse, or structured-logging fallback without keys) + FastAPI chat endpoint | 22 min | Req 7, 8; guardrails/observability |

Σ estimates = 79 min.

## Protected-concern disposition (fixed)

- **Guardrails / observability — triggered** (uploaded documents are untrusted content): Task 4.
- **Access control — triggered** (assignment names tenancy): `tenant_id` bound to an authenticated
  session, never a caller-supplied key: Tasks 1 (schema), 3 (pre-filter), 4 (endpoint auth).
- **Human-approval gate — N/A**: every mutation is a deterministic, hash-gated, user-initiated
  pipeline step on the caller's own content; no autonomous external side effect.

## Criteria coverage map

Reconstructed from the problem statement and rubric at Task 0, because the Step B table wasn't
available word for word at persist time. If it differs from the Step B chat version, the chat
version wins, and this table is corrected in its own commit.

| Source | Criterion | Evidence produced by |
|---|---|---|
| Problem §4 Req 1 | Polymorphic ingestion → Markdown | Task 2 |
| Problem §4 Req 2 | SHA-256 per page/tab, `page_registry` bypass | Tasks 1, 2 |
| Problem §4 Req 3 | Transactional cascading purge | Task 1 (repo), Task 2 (wired) |
| Problem §4 Req 4 | Tenant isolation + mandatory pre-filter | Tasks 1, 3, 4 |
| Problem §4 Req 5 | Structure-aware chunking, child→parent FK | Tasks 1, 2 |
| Problem §4 Req 6 | Dense ∥ sparse → RRF, parent assembly | Task 3 |
| Problem §4 Req 7 | Sufficiency check, re-query, control gate | Task 4 |
| Problem §4 Req 8 | RAG triad computed and logged | Task 4 |
| Problem §5 | Working prototype + chat interface | Task 4 (FastAPI endpoint) |
| Problem §5 | Architecture overview | `design_dialogue.md` + this file; Phase 3 |
| Problem §5 | Verification scenarios 1–3 | Tasks 2, 3/1, 4 tests; `docs/demo_script.md` (Phase 3) |
| Problem §5 | Setup: containers, schema provisioning | Task 1 (compose + schema script); Phase 3 README |
| Problem §6 | Polymorphic parsing effectiveness | Task 2 |
| Problem §6 | Architecture & system design quality | Tasks 1–3, design docs |
| Problem §6 | Security & multi-tenant rigor | Task 3 (cross-tenant leak test) |
| Problem §6 | Depth of self-correction | Task 4 |
| Rubric 1a | Design doc / diagram | `design_dialogue.md`, this file |
| Rubric 1b | Design-agent evidence | `design_dialogue.md` (committed at lock) |
| Rubric 2a | Robustness, structured logging, audit trail | Tasks 1–4; `utils/` logging |
| Rubric 2b | Dev-velocity evidence | `AI_ORCHESTRATION.md` rows per PR |
| Rubric 3a | Tests, mocks, coverage | every task; coverage report (Phase 2.5 item 2) |
| Rubric 3b | AI-executed test evidence | CI (Phase 2.5 item 1) + coverage report |
| Rubric 4a | Leadership doc | `docs/summary.md` (Phase 3) |
| Rubric 4b | Orchestration narrative | `AI_ORCHESTRATION.md` narrative (Phase 3) |

## Open items

- Docker daemon not responding at 21:49 (`docker ps` → 500 from `dockerDesktopLinuxEngine`); Task 1
  rung-2 test needs real Postgres.
- No model-provider credentials in the environment (no OpenAI/Anthropic/Groq/Voyage/Langfuse keys);
  the locked bundle names no embedding or LLM provider. Affects Tasks 2–4.
