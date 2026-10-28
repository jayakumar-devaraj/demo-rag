"""Helpers that run the real Alembic migrations against a real Postgres (integration tests only)."""

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[1]


def upgrade_head(url: str, app_role: str | None = None) -> None:
    """Run `alembic upgrade head` as the schema owner at `url`, granting to `app_role` when given."""
    saved = {k: os.environ.get(k) for k in ("MIGRATION_DATABASE_URL", "APP_DB_ROLE")}
    os.environ["MIGRATION_DATABASE_URL"] = url
    if app_role:
        os.environ["APP_DB_ROLE"] = app_role
    else:
        os.environ.pop("APP_DB_ROLE", None)
    try:
        command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def with_database(url: str, dbname: str) -> str:
    """Return `url` pointing at another database on the same server."""
    return urlunsplit(urlsplit(url)._replace(path=f"/{dbname}"))


def with_credentials(url: str, user: str, password: str) -> str:
    """Return `url` with other credentials."""
    parts = urlsplit(url)
    return urlunsplit(parts._replace(netloc=f"{user}:{password}@{parts.hostname}:{parts.port}"))


def unique(prefix: str) -> str:
    """A short unique identifier safe to use unquoted in SQL."""
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


@contextmanager
def scratch_database(admin_conn: object, base_url: str) -> Iterator[str]:
    """Create an empty database for one test and drop it afterwards; yields its owner-level URL."""
    from src.db.connection import run

    name = unique("mig")
    run(admin_conn.execute(f'CREATE DATABASE "{name}"'))
    try:
        yield with_database(base_url, name)
    finally:
        run(admin_conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
