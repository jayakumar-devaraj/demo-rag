# demo-rag

## Tech stack

Read from `pyproject.toml` / `uv.lock` and the running container.

- **Language / tooling:** Python 3.13.13, managed by uv 0.11.19. `uv sync` installs, and `uv run` runs everything.
- **Persistence:** PostgreSQL 16.14 with the pgvector extension 0.8.6 (image `pgvector/pgvector:pg16`, see `docker-compose.yml`). One store holds `page_registry` and `chunks`, so the hash gate, the purge and the upsert happen in one transaction.
- **DB driver:** psycopg[binary] 3.3.6, async connections (`src/db/connection.py`).
- **Vector adapter:** pgvector (Python) 0.5.0; `Vector.to_text()` serializes embeddings for `::vector` inserts.
- **Validation:** pydantic 2.13.5, which validates `PageIn` / `ChunkIn` at the repository boundary (SHA-256 format, embedding dimension, non-empty tenant).
- **Testing:** pytest 9.1.1. Tests marked `integration` run against the real Postgres from `docker-compose.yml`.
- **Document parsing:** pymupdf 1.28.2 handles PDF pages; `find_tables()` turns table grids into Markdown and the remaining text blocks are kept in reading order. openpyxl 3.1.5 handles XLSX: one unit per tab, cells labelled with Excel row numbers and column letters, and formulas kept when no cached value exists. python-docx 1.2.0 handles DOCX headings, paragraphs and inline tables in document order.
- **Retrieval / security:** a single SQL statement runs a dense pgvector cosine leg and a sparse Postgres FTS leg (`simple` config, which keeps identifiers intact), both filtered by the session's `tenant_id`. Results are fused in Python with Reciprocal Rank Fusion (k=60), then parent pages/tabs are reassembled through `chunks.page_id`. Bearer tokens map to tenants server-side (`TENANT_TOKENS`, compared with `hmac.compare_digest`); no new dependency.
- **Orchestration:** langgraph 1.2.12 with langchain-core 1.6.5 runs retrieve, then evaluate, then either re-query (up to 3 retrieval passes) and retrieve again, or synthesize, or fall back (`src/orchestration/graph.py`). Every node emits a structured-log span.
- **Model provider (optional):** langchain-openai 1.6.6 supplies `ChatOpenAI` and `OpenAIEmbeddings` (384 dimensions). It's enabled only when `OPENAI_API_KEY`, `OPENAI_CHAT_MODEL` and `OPENAI_EMBEDDING_MODEL` are all set; otherwise `/ingest` and `/chat` return 503.
- **Guardrails:** retrieved text sits only in an escaped `<documents>` data block, never in system instructions. LLM output is parsed into Pydantic models (`SufficiencyVerdict`, `Answer`). A numeric-groundedness gate replaces any answer containing numbers absent from the context with the insufficient-data message.
- **Evaluation:** ragas 0.4.3 computes ContextRelevance, Faithfulness (groundedness) and ResponseRelevancy per answer when a provider is configured; the deterministic numeric groundedness score is always computed. The triad is logged as `chat_answered` and returned in the response.
- **Observability:** JSON-line logging (`src/utils/observability.py`). langfuse 4.15.6 LangChain callback when `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are set.
- **API:** fastapi 0.141.1, served by uvicorn 0.53.0 on a SelectorEventLoop (`uv run python -m src.main`). python-multipart 0.0.32 handles uploads. 401 for a bad token, 413/415/422 for bad input, 503/504 for infrastructure failures.
