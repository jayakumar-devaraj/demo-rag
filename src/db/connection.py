"""Async Postgres connection helpers."""

import asyncio
import os
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import psycopg

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
EMBEDDING_DIM = 384
CONNECT_TIMEOUT_S = 5


def database_url() -> str:
    """Return DATABASE_URL, failing loudly when it is unset."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set (see .env.example)")
    return url


async def connect() -> psycopg.AsyncConnection:
    """Open an async connection with an explicit connect timeout."""
    return await psycopg.AsyncConnection.connect(database_url(), connect_timeout=CONNECT_TIMEOUT_S)


async def apply_schema(conn: psycopg.AsyncConnection) -> None:
    """Apply the idempotent schema script in one transaction."""
    async with conn.transaction():
        await conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine on a SelectorEventLoop (psycopg async cannot use Windows' Proactor loop)."""
    return asyncio.run(coro, loop_factory=asyncio.SelectorEventLoop)
