# AI Orchestration Log

Build-process record: one row per merge. Runtime audit trail is separate (application logs).

| task | generated (code/tests/docs) | branch | PR # |
|---|---|---|---|
| 0 — persist plan (+ Step A dialogue) | docs: master_plan, specs task-1..4, AI_ORCHESTRATION, known_limitations; design_dialogue append | plan/setup | #1 |
| 1 — schema + transactional repo | code: src/db/{schema.sql,connection.py,repository.py}, docker-compose, pyproject/uv.lock; tests: 3 unit + 4 integration (real Postgres); docs: README Tech Stack, CLAUDE.md committed | task-1-schema-repo | #2 |
| 2 — polymorphic ingestion | code: src/ingestion/{workers,chunking,pipeline}.py, repo rename/purge helpers; tests: 9 unit + 3 integration (real Postgres), test-double embedder; docs: README Tech Stack | task-2-polymorphic-ingestion | #3 |
| 3 — hybrid retrieval | code: src/retrieval/hybrid.py, src/utils/auth.py; tests: 8 unit + 2 integration (real Postgres: cross-tenant isolation, exact-ID recall, parent assembly); docs: README Tech Stack, .env.example | task-3-hybrid-retrieval | #4 |
| 4 — agentic loop + API (UNVERIFIED: live LLM/embedding/Ragas/Langfuse, no credentials) | code: src/agents/synthesis.py, src/orchestration/{state,graph}.py, src/eval/triad.py, src/routers/api.py, src/utils/observability.py, src/main.py, autocommit fix; tests: 13 unit + 2 integration (HTTP e2e, commit visibility); docs: README, known_limitations, .env.example | task-4-agentic-loop-api | #5 |
| H1 — CI | ci: .github/workflows/ci.yml (uv + pgvector service); code: schema provisioned at app startup (lifespan) after CI exposed it; tests: e2e runs lifespan; docs: README | hardening/ci | #6 |
| H2 — coverage | ci: --cov in workflow; tests: coverage report docs/coverage/coverage_report.txt (88%, 44 passed); deps: pytest-cov 7.1.0 | hardening/coverage | #7 |
| H3 — criteria tests | tests: triad scored/logged/returned (integration), JSON audit log + provider wiring (unit); coverage report refreshed (92%, 49 passed) | hardening/criteria-tests | #8 |
| H4 — reconcile | docs: master_plan/known_limitations corrected to match the build; VERIFY markers accounted for; planning miss logged | hardening/reconcile | #9 |
| wrap-up | docs: README Architecture/Quickstart/Local dev/Testing/CI, demo_script, summary, this narrative, known_limitations finalized | wrapup | #10 |

## Orchestration narrative

The build used Claude Code as the agent throughout. The human lead owned every gate (Gate A, Gate B,
the drift cut, the no-credentials option, the Phase 3 go-ahead). Step 0 and Step A commits carry a
Claude Sonnet 5 co-author trailer (`8468411`, `2c4653f`). The session resumed at Task 0 ran on Claude
Opus 5.5. `ef1878d` carries its trailer; later commits omit trailers per `CLAUDE.md` §5.

**Design (21:34–21:54).** The agent committed the governing prompt verbatim (`8468411`). It proposed
three stack bundles with live-resolved versions, one shared architecture diagram, and per-library
questions. The lead replied "Bundle 1", which locked it (`docs/design_dialogue.md`, Gate A; `2c4653f`).
After the lock, the agent appended one note recording the Python 3.13.13 pin instead of the 3.12 in
the lock text. Task 0 persisted the roadmap, the criteria map (reconstructed from the problem statement
and rubric, and labeled so), the per-task specs, this log and the limitations file (`ef1878d`; row 0,
PR #1). At the Task 1 checkpoint the drift check failed by 5 minutes. The agent proposed a Task 2 cut,
the lead accepted it, and it was recorded in the plan in the same commit (`861edd4`).

**Development (21:58–22:16).** Each task followed the same loop: recap line with a clock read, drift
check, feature branch, tests against the real dependency, commit, PR, orchestration row, self-merge.
- **Task 1** (row 1, PR #2, `814604e`): schema, and a repository that swaps a page's chunks in one
  transaction. A mid-transaction failure test proves the rollback.
- **Task 2** (row 2, PR #3, `e43dbd5`): format workers, SHA-256 gate, chunker. The first run failed
  one chunker test (a heading was emitted as an empty chunk). The code was fixed and the test left
  unchanged.
- **Task 3** (row 3, PR #4, `4bd3eed`): tenant-bound hybrid retrieval with a cross-tenant leak test.
- **Task 4** (row 4, PR #5, `173118e`): graph, guarded synthesis, triad, API. Its HTTP end-to-end test
  exposed a Task 1 defect: writes were savepoints rolled back on close. The fix is `autocommit=True`,
  plus a regression test on a fresh connection. The agent found a Ragas/langchain-community import
  incompatibility, shimmed it, and flagged it along with three stack additions in
  `known_limitations.md`. Live-model paths are marked UNVERIFIED.

**QA and hardening (22:16–22:27).**
- **H1 CI** (row H1, PR #6): the first CI run failed on a fresh database. The app relied on the Docker
  init script for its schema. The agent reproduced this locally on an empty database, fixed it with
  startup provisioning (`87c25a7`), and CI then passed 44 of 44.
- **H2** (row H2, PR #7, `5520d96`): committed coverage report, 88%.
- **H3** (row H3, PR #8, `6b63eee`): direct tests for Req 8 triad logging and the audit log. 49
  passed, 92% coverage.
- **H4** (row H4, PR #9): reconciled the plan and limitations with the build. The unused reserve was
  logged as a planning miss.

**Wrap-up (22:27–).** README setup sections were verified from a fresh clone of `main` at `857fc00`:
`uv sync --locked`, then 49 passed, then the server started and `/health` answered. The five
demo-scenario tests were re-run (5 passed). The summary and demo script were added.
| chore — AGENTS.md | docs: AGENTS.md committed; 10 untracked empty scaffold stubs deleted locally (not in diff) | chore/agents-md-and-stub-cleanup | #11 |
| H5 — live verification attempt (BLOCKED: OpenAI 429 insufficient_quota) | tests: 3 live scenario tests (marker live, excluded by default), 2 regression tests; code: Ragas shim ordering fix, provider errors → 503; docs: README, known_limitations, coverage (92%, 51 passed) | hardening/live-llm-verification | #12 |
| docs — architecture | docs: docs/architecture.md (4 Mermaid diagrams, design sections, error table, trade-offs, scaling), README link; code: triad timeout/provider error no longer 500; tests: triad-timeout integration test (52 passed) | docs/architecture | #13 |
| obs — request ID | code: X-Request-ID middleware + ContextVar log-record factory; tests: echo/stamp + unsafe-ID replacement (54 passed); real-server smoke; docs: architecture, README | observability/request-id | #14 |
| docs — README description | docs: README project description; also records PR #15 (providers/groq-primary) as open and unmerged at the hard stop | docs/readme-description | #16 |
| providers — Groq primary + OpenAI fallback, fastembed | code: src/main.py FallbackLLM/ChatGroq/fastembed, Groq errors → 503, Ragas name adapter, triad strictness=1 + sequential metrics; tests: provider wiring + fallback unit tests, live scenarios 3 passed against Groq; default 56 passed (06038e1 message says 57 by mistake); docs: README, .env.example, known_limitations | providers/groq-primary | #15 |
| docs — live demo script | docs: demo_script.md rewritten around the live Groq run (3 passed, 79 s) with offline fallback (re-verified 5 passed) | docs/demo-live-groq | #17 |
