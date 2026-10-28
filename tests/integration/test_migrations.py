"""Migrations and the role split against a real Postgres: fresh install, adoption, least privilege."""

import psycopg
import pytest

from src.db.connection import EXPECTED_SCHEMA_REVISION, database_url, require_schema_revision, run
from tests.migration_support import (
    ROOT,
    scratch_database,
    unique,
    upgrade_head,
    with_credentials,
)

pytestmark = pytest.mark.integration

BASELINE_SQL = (ROOT / "migrations" / "baseline_schema.sql").read_text(encoding="utf-8")
SHA = "a" * 64


async def _fetch(url: str, sql: str, tenant: str | None = None) -> list[tuple]:
    async with await psycopg.AsyncConnection.connect(url, autocommit=True) as c:
        if tenant is not None:
            await c.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant,))
        return await (await c.execute(sql)).fetchall()


async def _run_script(url: str, sql: str) -> None:
    async with await psycopg.AsyncConnection.connect(url, autocommit=True) as c:
        await c.execute(sql)


def test_fresh_database_reaches_head_and_upgrade_is_idempotent(conn):
    with scratch_database(conn, database_url()) as url:
        upgrade_head(url)
        upgrade_head(url)
        tables = {r[0] for r in run(_fetch(url, "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))}
        revision = run(_fetch(url, "SELECT version_num FROM alembic_version"))
    assert {"page_registry", "chunks", "audit_log", "alembic_version"} <= tables
    assert revision == [(EXPECTED_SCHEMA_REVISION,)]


def test_database_from_the_old_init_script_is_adopted_without_data_loss(conn):
    with scratch_database(conn, database_url()) as url:
        run(_run_script(url, BASELINE_SQL))
        run(_fetch(url, f"INSERT INTO page_registry (tenant_id, doc_key, page_key, sha256, source_name, markdown) VALUES ('t', 'd', 'p', '{SHA}', 's', 'm') RETURNING 1"))
        upgrade_head(url)
        rows = run(_fetch(url, "SELECT tenant_id, doc_key FROM page_registry"))
    assert rows == [("t", "d")]


def test_app_role_has_least_privilege(conn):
    role, password = unique("app"), unique("pw")
    run(conn.execute(f"CREATE ROLE {role} LOGIN PASSWORD '{password}' NOSUPERUSER NOCREATEDB NOCREATEROLE"))
    try:
        with scratch_database(conn, database_url()) as owner_url:
            upgrade_head(owner_url, app_role=role)
            app_url = with_credentials(owner_url, role, password)
            run(_fetch(app_url, "INSERT INTO audit_log (tenant_id, subject, action, outcome) VALUES ('t', 's', 'chat', 'ok') RETURNING 1", "t"))
            assert run(_fetch(app_url, "SELECT count(*) FROM audit_log", "t")) == [(1,)]
            for denied in (
                "UPDATE audit_log SET outcome = 'error'",
                "DELETE FROM audit_log",
                "TRUNCATE audit_log",
                "CREATE TABLE sneaky (x int)",
                "DROP TABLE chunks",
            ):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    run(_fetch(app_url, denied))
            run(_fetch(app_url, f"INSERT INTO page_registry (tenant_id, doc_key, page_key, sha256, source_name, markdown) VALUES ('t', 'd', 'p', '{SHA}', 's', 'm') RETURNING 1", "t"))
            run(_fetch(app_url, "DELETE FROM page_registry RETURNING 1", "t"))

            async def check() -> None:
                async with await psycopg.AsyncConnection.connect(app_url, autocommit=True) as c:
                    await require_schema_revision(c)

            run(check())
    finally:
        run(conn.execute(f"DROP ROLE IF EXISTS {role}"))


def test_unmigrated_or_wrong_revision_fails_startup_check(conn):
    async def check(url: str) -> None:
        async with await psycopg.AsyncConnection.connect(url, autocommit=True) as c:
            await require_schema_revision(c)

    with scratch_database(conn, database_url()) as url:
        with pytest.raises(RuntimeError, match="not migrated"):
            run(check(url))
        upgrade_head(url)
        run(_fetch(url, "UPDATE alembic_version SET version_num = 'stale' RETURNING 1"))
        with pytest.raises(RuntimeError, match="expected"):
            run(check(url))
