-- Schema provisioning: mounted as a docker-entrypoint init script and applied by src/db/connection.py.
CREATE EXTENSION IF NOT EXISTS vector;

-- One row per normalized page (PDF/DOCX/TXT) or tab (XLSX). sha256 is taken over the Markdown
-- before chunking; tenant_id is the isolation key for every read.
CREATE TABLE IF NOT EXISTS page_registry (
    id          BIGSERIAL PRIMARY KEY,
    tenant_id   TEXT        NOT NULL,
    doc_key     TEXT        NOT NULL,
    page_key    TEXT        NOT NULL,
    sha256      CHAR(64)    NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    source_name TEXT        NOT NULL,
    markdown    TEXT        NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, doc_key, page_key)
);
CREATE INDEX IF NOT EXISTS page_registry_tenant_hash ON page_registry (tenant_id, sha256);

-- Child chunks: deleting a page cascades to its chunks, so a changed page can never leave
-- orphan vectors behind.
CREATE TABLE IF NOT EXISTS chunks (
    id        BIGSERIAL PRIMARY KEY,
    tenant_id TEXT        NOT NULL,
    page_id   BIGINT      NOT NULL REFERENCES page_registry (id) ON DELETE CASCADE,
    ord       INT         NOT NULL,
    content   TEXT        NOT NULL,
    embedding vector(384) NOT NULL,
    tsv       tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED
);
CREATE INDEX IF NOT EXISTS chunks_tenant ON chunks (tenant_id);
CREATE INDEX IF NOT EXISTS chunks_page ON chunks (page_id);
CREATE INDEX IF NOT EXISTS chunks_tsv ON chunks USING GIN (tsv);
CREATE INDEX IF NOT EXISTS chunks_embedding ON chunks USING hnsw (embedding vector_cosine_ops);
