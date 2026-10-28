"""Async Postgres connection helpers."""

import asyncio
import os
from collections.abc import Coroutine
from typing import Any

import psycopg

EMBEDDING_DIM = 384
CONNECT_TIMEOUT_S = 5
# The Alembic head this code was written against; a unit test keeps it equal to migrations/versions.
EXPECTED_SCHEMA_REVISION = "0002"


def database_url() -> str:
    """Return DATABASE_URL, failing loudly when it is unset."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set (see .env.example)")
    return url


async def connect() -> psycopg.AsyncConnection:
    """Open an async connection with an explicit connect timeout.

    autocommit=True makes every `conn.transaction()` block a real BEGIN/COMMIT; without it the
    first statement opens an implicit transaction and those blocks degrade to savepoints that are
    rolled back when the connection closes.
    """
    return await psycopg.AsyncConnection.connect(database_url(), connect_timeout=CONNECT_TIMEOUT_S, autocommit=True)


async def require_schema_revision(conn: psycopg.AsyncConnection) -> None:
    """Fail startup unless the database is at the revision this code expects (the API never migrates)."""
    try:
        cur = await conn.execute("SELECT version_num FROM alembic_version")
    except psycopg.errors.UndefinedTable:
        raise RuntimeError("database is not migrated: run the migrate job (alembic upgrade head)") from None
    rows = await cur.fetchall()
    found = [r[0] for r in rows]
    if found != [EXPECTED_SCHEMA_REVISION]:
        raise RuntimeError(f"schema revision {found} != expected {EXPECTED_SCHEMA_REVISION}: run the migrate job")


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine on a SelectorEventLoop (psycopg async cannot use Windows' Proactor loop)."""
    return asyncio.run(coro, loop_factory=asyncio.SelectorEventLoop)
