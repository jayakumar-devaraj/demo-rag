"""Async Postgres connection helpers."""

import asyncio
import os
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from typing import Any

import psycopg
from psycopg_pool import AsyncConnectionPool

EMBEDDING_DIM = 384
CONNECT_TIMEOUT_S = 5
POOL_MAX_SIZE = 10  # per API replica; Postgres allows 100 connections by default
# The Alembic head this code was written against; a unit test keeps it equal to migrations/versions.
EXPECTED_SCHEMA_REVISION = "0003"


def database_url() -> str:
    """Return DATABASE_URL, failing loudly when it is unset."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set (see .env.example)")
    return url


async def connect(tenant_id: str | None = None) -> psycopg.AsyncConnection:
    """Open an async connection with an explicit connect timeout, bound to `tenant_id` when given.

    The tenant is stored in the session setting `app.tenant_id`, which the row-level-security policies
    compare against (migration 0003). It is session-level, not SET LOCAL, because the connection is
    autocommit and is closed by its caller. The API does not use this: it binds per transaction on pooled
    connections (`tenant_transaction`).

    autocommit=True makes every `conn.transaction()` block a real BEGIN/COMMIT; without it the
    first statement opens an implicit transaction and those blocks degrade to savepoints that are
    rolled back when the connection closes.
    """
    conn = await psycopg.AsyncConnection.connect(database_url(), connect_timeout=CONNECT_TIMEOUT_S, autocommit=True)
    if tenant_id is not None:
        try:
            await conn.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant_id,))
        except BaseException:
            await conn.close()
            raise
    return conn


def open_pool(url: str | None = None, max_size: int = POOL_MAX_SIZE) -> AsyncConnectionPool:
    """An unopened pool of autocommit connections (the API opens it at startup); waits for a free one fail with
    `PoolTimeout`, an `OperationalError`, after CONNECT_TIMEOUT_S."""
    return AsyncConnectionPool(url or database_url(), min_size=1, max_size=max_size, timeout=CONNECT_TIMEOUT_S,
                               kwargs={"autocommit": True, "connect_timeout": CONNECT_TIMEOUT_S}, open=False)


@asynccontextmanager
async def tenant_transaction(pool: AsyncConnectionPool, tenant_id: str) -> AsyncIterator[psycopg.AsyncConnection]:
    """A pooled connection inside one transaction bound to `tenant_id` for the row-level-security policies.

    The binding is transaction-local (`set_config(..., true)`), so it ends at COMMIT or ROLLBACK and the
    connection's next borrower starts with no tenant, which sees no rows. Blocks opened with
    `conn.transaction()` inside it are savepoints of this transaction.
    """
    async with pool.connection() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
        yield conn


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
