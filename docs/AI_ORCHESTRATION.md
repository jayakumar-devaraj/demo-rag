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
