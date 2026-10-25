# multi-tenant-agentic-rag

A multi-tenant, self-correcting RAG service that answers questions about PDFs, Excel workbooks, Word
documents and text files. It converts every page or sheet into table-preserving Markdown and uses
per-page SHA-256 fingerprints to skip unchanged content and atomically purge stale chunks. Search
combines vector and keyword ranking, and every query is restricted to the caller's own tenant.
Answers come from a LangGraph loop that re-queries for missing facts, declines when the evidence is
insufficient, blocks numbers not found in the sources, and scores every answer on the RAG triad.

## Tech stack

Read from `pyproject.toml` / `uv.lock` and the running container.

- **Language / tooling:** Python 3.13.13, managed by uv 0.11.19. `uv sync` installs, and `uv run` runs everything.
- **Persistence:** PostgreSQL 16.14 with the pgvector extension 0.8.6 (image `pgvector/pgvector:pg16`, see `docker-compose.yml`). The idempotent `src/db/schema.sql` is applied at app startup (FastAPI lifespan) and by the compose init script. One store holds `page_registry` and `chunks`, so the hash gate, the purge and the upsert happen in one transaction.
- **DB driver:** psycopg[binary] 3.3.6, async connections (`src/db/connection.py`).
- **Vector adapter:** pgvector (Python) 0.5.0; `Vector.to_text()` serializes embeddings for `::vector` inserts.
- **Validation:** pydantic 2.13.5, which validates `PageIn` / `ChunkIn` at the repository boundary (SHA-256 format, embedding dimension, non-empty tenant).
- **Testing:** pytest 9.1.1. Tests marked `integration` run against the real Postgres from `docker-compose.yml`.
- **Document parsing:** pymupdf 1.28.2 handles PDF pages; `find_tables()` turns table grids into Markdown and the remaining text blocks are kept in reading order. openpyxl 3.1.5 handles XLSX: one unit per tab, cells labelled with Excel row numbers and column letters, and formulas kept when no cached value exists. python-docx 1.2.0 handles DOCX headings, paragraphs and inline tables in document order.
- **Retrieval / security:** a single SQL statement runs a dense pgvector cosine leg and a sparse Postgres FTS leg (`simple` config, which keeps identifiers intact), both filtered by the session's `tenant_id`. Results are fused in Python with Reciprocal Rank Fusion (k=60), then parent pages/tabs are reassembled through `chunks.page_id`. Bearer tokens map to tenants server-side (`TENANT_TOKENS`, compared with `hmac.compare_digest`); no new dependency.
- **Orchestration:** langgraph 1.2.12 with langchain-core 1.6.5 runs retrieve, then evaluate, then either re-query (up to 3 retrieval passes) and retrieve again, or synthesize, or fall back (`src/orchestration/graph.py`). Every node emits a structured-log span.
- **Model providers (optional):** langchain-groq 1.1.3 (`ChatGroq`, primary chat via `GROQ_API_KEY` + `GROQ_CHAT_MODEL`) with langchain-openai 1.6.6 (`ChatOpenAI`) as the fallback, wrapped by `FallbackLLM`. fastembed 0.8.1 provides local embeddings (`FASTEMBED_MODEL`, which must give 384 dimensions, checked at startup), with OpenAI embeddings used if it is unset. Without a chat model and embeddings, `/ingest` and `/chat` return 503.
- **Guardrails:** retrieved text sits only in an escaped `<documents>` data block, never in system instructions. LLM output is parsed into Pydantic models (`SufficiencyVerdict`, `Answer`). A numeric-groundedness gate replaces any answer containing numbers absent from the context with the insufficient-data message.
- **Evaluation:** ragas 0.4.3 computes ContextRelevance, Faithfulness (groundedness) and ResponseRelevancy per answer when a provider is configured; the deterministic numeric groundedness score is always computed. The triad is logged as `chat_answered` and returned in the response.
- **Observability:** JSON-line logging (`src/utils/observability.py`); each line carries the `request_id` (echoed as the `X-Request-ID` header). langfuse 4.15.6 LangChain callback when `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are set.
- **API:** fastapi 0.141.1, served by uvicorn 0.53.0 on a SelectorEventLoop (`uv run python -m src.main`). python-multipart 0.0.32 handles uploads. 401 for a bad token, 413/415/422 for bad input, 503/504 for infrastructure failures. `POST /v1/retrieve` is the versioned retrieval-only endpoint; `RETRIEVE_LLM_MAX_RETRIES` (default 0) sets its provider-client retries, while `/chat` keeps 2.
- **CI:** GitHub Actions (`.github/workflows/ci.yml`) runs `uv sync --locked` and `uv run pytest` on every push and PR, against a `pgvector/pgvector:pg16` service container. It uses actions/checkout v7.0.1, astral-sh/setup-uv v10.2.0 and uv 0.11.19.
- **Coverage:** pytest-cov 7.1.0. CI prints a term-missing report; the committed snapshot is `docs/coverage/coverage_report.txt`.

## Architecture

The as-built architecture (diagrams, schema, hybrid ranking, loop gates, error boundaries, trade-offs, scaling) is in [`docs/architecture.md`](docs/architecture.md). The Step A data-flow diagram is in [`docs/design_dialogue.md`](docs/design_dialogue.md#architecture-shared-across-all-three-bundles), and the module layout is in [`docs/master_plan.md`](docs/master_plan.md#layout-binding-for-rule-10). In short: upload → format worker (`src/ingestion/workers.py`) → SHA-256 gate and chunking (`src/ingestion/`) → transactional write (`src/db/repository.py`). A question goes → token-bound tenant (`src/utils/auth.py`) → hybrid retrieval with RRF (`src/retrieval/hybrid.py`) → the LangGraph self-correction loop (`src/orchestration/graph.py`) → guarded synthesis (`src/agents/synthesis.py`) → RAG triad (`src/eval/triad.py`).

## Quickstart

Prerequisites: Docker, and [uv](https://docs.astral.sh/uv/) (it installs Python 3.13.13 itself).

```bash
cp .env.example .env
```

In `.env`, set `POSTGRES_PASSWORD` and make `DATABASE_URL` use the same password. Set `TENANT_TOKENS` to `token:tenant` pairs. For live answers, also set `OPENAI_API_KEY`, `OPENAI_CHAT_MODEL` and `OPENAI_EMBEDDING_MODEL`; without them, `/ingest` and `/chat` return 503.

```bash
docker compose -p multi-tenant-agentic-rag --env-file .env up -d
```

```bash
uv sync --locked
```

```bash
uv run --env-file .env python -m src.main
```

The API listens on `http://127.0.0.1:8000`, and the schema is applied at startup:

- `GET /health` reports which providers are configured.
- `POST /ingest` takes a multipart `file` (.pdf, .xlsx, .docx or .txt) and `Authorization: Bearer <token>`.
- `POST /chat` takes `{"question": "..."}` with the same header. The tenant comes only from the token.
- `POST /v1/retrieve` takes the same body and header and returns the retrieved passages without writing an answer. Send `X-Timeout-Ms` to set a deadline. The contract is [`docs/openapi-v1.json`](docs/openapi-v1.json); regenerate it with `uv run python -m src.routers.contract`.

To run the API as a container instead (non-root, embedding model baked in, healthcheck on `/health`), set `TENANT_TOKENS` and the provider variables in `.env`, then:

```bash
docker compose -p demo-rag --env-file .env up -d --build rag-api
```

It listens on `RAG_API_PORT` (default 8000) and reaches Postgres over the compose network.

## Local development

Code lives under `src/<responsibility>/`, per the layout in `docs/master_plan.md`. Add dependencies with `uv add <pkg>==<version>`, keep `uv.lock` committed, and update the Tech stack section above in the same commit. Logs are JSON lines on stderr, and every graph node logs a `node` span.

## Testing

```bash
uv run --env-file .env pytest -q --cov=src --cov-report=term-missing
```

Tests marked `integration` use the real Postgres from `docker-compose.yml`. Tests marked `live` call the real OpenAI models and are excluded by default; run them with `uv run --env-file .env pytest -m live -s` once `OPENAI_*` is set in `.env`. LLM and embedding calls in tests use labeled test doubles (`tests/conftest.py`). The last committed coverage report is `docs/coverage/coverage_report.txt`.

## Deployment/CI

`.github/workflows/ci.yml` runs `uv sync --locked` and the full suite, with coverage, against a `pgvector/pgvector:pg16` service on every push and PR. No deployment target is configured.

`.github/workflows/publish.yml` runs on a pushed `v*` tag: it calls the CI workflow, then builds the `Dockerfile` and publishes `ghcr.io/jayakumar-devaraj/multi-tenant-agentic-rag:<tag>` using actions docker/setup-buildx-action v4.4.1, docker/login-action v4.6.0, docker/metadata-action v6.2.0 and docker/build-push-action v7.4.0. The image embeds the `FASTEMBED_MODEL` build argument (default `BAAI/bge-small-en-v1.5`); run it with the same `FASTEMBED_MODEL`, since the cache is read-only for the non-root user.
