# demo-rag

## Tech stack

Read from `pyproject.toml` / `uv.lock` and the running container.

- **Language / tooling:** Python 3.13.13, managed by uv 0.11.19. `uv sync` installs, and `uv run` runs everything.
- **Persistence:** PostgreSQL 16.14 with the pgvector extension 0.8.6 (image `pgvector/pgvector:pg16`, see `docker-compose.yml`). One store holds `page_registry` and `chunks`, so the hash gate, the purge and the upsert happen in one transaction.
- **DB driver:** psycopg[binary] 3.3.6, async connections (`src/db/connection.py`).
- **Vector adapter:** pgvector (Python) 0.5.0; `Vector.to_text()` serializes embeddings for `::vector` inserts.
- **Validation:** pydantic 2.13.5, which validates `PageIn` / `ChunkIn` at the repository boundary (SHA-256 format, embedding dimension, non-empty tenant).
- **Testing:** pytest 9.1.1. Tests marked `integration` run against the real Postgres from `docker-compose.yml`.
