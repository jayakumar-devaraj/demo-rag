"""Durable audit trail table (ADR 0002). Rows are written by the API; the app role cannot change them.

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            id          BIGSERIAL   PRIMARY KEY,
            ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
            tenant_id   TEXT        NOT NULL,
            subject     TEXT        NOT NULL,
            action      TEXT        NOT NULL CHECK (action IN ('ingest', 'delete', 'retrieve', 'chat')),
            doc_key     TEXT,
            outcome     TEXT        NOT NULL CHECK (outcome IN ('ok', 'denied', 'error')),
            request_id  TEXT,
            traceparent TEXT,
            detail      JSONB       NOT NULL DEFAULT '{}'::jsonb
        );
        CREATE INDEX IF NOT EXISTS audit_log_tenant_ts ON audit_log (tenant_id, ts DESC);
        """
    )
