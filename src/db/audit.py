"""Durable audit trail (ADR 0002): one `audit_log` row per ingest, retrieve or chat, tenant-scoped on read."""

from datetime import datetime
from typing import Any, Literal

import psycopg
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from src.utils.auth import Session
from src.utils.observability import request_id, traceparent

Action = Literal["ingest", "delete", "retrieve", "chat"]
Outcome = Literal["ok", "denied", "error"]


class AuditEntry(BaseModel):
    """One audit row as returned to the owning tenant. Never contains question or document text."""

    id: int
    ts: datetime
    subject: str
    action: Action
    doc_key: str | None
    outcome: Outcome
    request_id: str | None
    traceparent: str | None
    detail: dict[str, Any]


async def record_audit(conn: psycopg.AsyncConnection, session: Session, action: Action, outcome: Outcome,
                       doc_key: str | None = None, detail: dict[str, Any] | None = None) -> None:
    """Insert one audit row on `conn`; inside a transaction it commits or rolls back with the caller's work."""
    await conn.execute(
        "INSERT INTO audit_log (tenant_id, subject, action, doc_key, outcome, request_id, traceparent, detail) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (session.tenant_id, session.subject, action, doc_key, outcome, request_id.get(), traceparent.get(),
         Jsonb(detail or {})))


async def list_audit(conn: psycopg.AsyncConnection, tenant_id: str, limit: int, before_id: int | None = None,
                     action: Action | None = None) -> list[AuditEntry]:
    """Newest-first audit rows for one tenant; `before_id` is the cursor from the previous page."""
    cur = await conn.execute(
        "SELECT id, ts, subject, action, doc_key, outcome, request_id, traceparent, detail FROM audit_log "
        "WHERE tenant_id = %(tenant)s AND (%(before)s::bigint IS NULL OR id < %(before)s) "
        "AND (%(action)s::text IS NULL OR action = %(action)s) ORDER BY id DESC LIMIT %(limit)s",
        {"tenant": tenant_id, "before": before_id, "action": action, "limit": limit})
    return [AuditEntry(id=r[0], ts=r[1], subject=r[2], action=r[3], doc_key=r[4], outcome=r[5], request_id=r[6],
                       traceparent=r[7], detail=r[8]) for r in await cur.fetchall()]
