"""Row-level security on a real Postgres, as the non-owner app role: the database confines every query."""

from collections.abc import Iterator

import psycopg
import pytest

from src.db.connection import connect, database_url, open_pool, run, tenant_transaction
from src.retrieval.hybrid import retrieve
from src.utils.auth import Session
from tests.conftest import FakeEmbedder
from tests.migration_support import scratch_database, unique, upgrade_head, with_credentials

pytestmark = pytest.mark.integration

SHA = "a" * 64
TENANTS = ("alpha", "bravo")
VECTOR = "array_fill(0.1::real, ARRAY[384])::vector"


async def _connect_as(url: str, tenant: str | None) -> psycopg.AsyncConnection:
    """Open a connection, bound to `tenant` the way the API binds it."""
    c = await psycopg.AsyncConnection.connect(url, autocommit=True)
    if tenant is not None:
        await c.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant,))
    return c


async def _exec(url: str, sql: str, tenant: str | None = None) -> list[tuple]:
    """Run one statement on a fresh connection; a statement without a result set returns [(rowcount,)]."""
    async with await _connect_as(url, tenant) as c:
        cur = await c.execute(sql)
        return await cur.fetchall() if cur.description else [(cur.rowcount,)]


@pytest.fixture(scope="module")
def db() -> Iterator[tuple[str, str]]:
    """A migrated scratch database with two tenants' rows (seeded as owner) and an app-role URL."""
    admin = run(connect())
    role, password = unique("app"), unique("pw")
    run(admin.execute(f"CREATE ROLE {role} LOGIN PASSWORD '{password}' NOSUPERUSER NOCREATEDB NOCREATEROLE"))
    try:
        with scratch_database(admin, database_url()) as owner_url:
            upgrade_head(owner_url, app_role=role)
            for t in TENANTS:
                run(_exec(owner_url,
                          f"WITH p AS (INSERT INTO page_registry (tenant_id, doc_key, page_key, sha256, source_name, "
                          f"markdown) VALUES ('{t}', 'd', 'p', '{SHA}', 's', 'text of {t}') RETURNING id), "
                          f"c AS (INSERT INTO chunks (tenant_id, page_id, ord, content, embedding) "
                          f"SELECT '{t}', id, 0, 'alpha bravo {t}', {VECTOR} FROM p RETURNING id) "
                          f"INSERT INTO audit_log (tenant_id, subject, action, outcome) "
                          f"VALUES ('{t}', 's', 'chat', 'ok') RETURNING 1"))
            yield owner_url, with_credentials(owner_url, role, password)
    finally:
        run(admin.execute(f"DROP ROLE IF EXISTS {role}"))
        run(admin.close())


def test_policies_are_forced_and_the_app_role_cannot_bypass_them(db):
    owner_url, _ = db
    flags = run(_exec(owner_url, "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
                                 "WHERE relname IN ('page_registry', 'chunks', 'audit_log') ORDER BY relname"))
    assert flags == [("audit_log", True, True), ("chunks", True, True), ("page_registry", True, True)]
    assert run(_exec(owner_url, "SELECT bool_or(rolsuper OR rolbypassrls) FROM pg_roles "
                                "WHERE rolname LIKE 'app\\_%'")) == [(False,)]


@pytest.mark.parametrize("table", ["page_registry", "chunks", "audit_log"])
def test_a_connection_with_no_tenant_sees_nothing(db, table):
    _, app_url = db
    assert run(_exec(app_url, f"SELECT count(*) FROM {table}")) == [(0,)]


@pytest.mark.parametrize("table", ["page_registry", "chunks", "audit_log"])
def test_a_statement_without_a_tenant_predicate_sees_only_the_bound_tenant(db, table):
    _, app_url = db
    for t in TENANTS:
        assert run(_exec(app_url, f"SELECT DISTINCT tenant_id FROM {table}", tenant=t)) == [(t,)]


def test_writing_another_tenants_rows_is_denied(db):
    _, app_url = db
    for sql in (
        f"INSERT INTO page_registry (tenant_id, doc_key, page_key, sha256, source_name, markdown) "
        f"VALUES ('bravo', 'x', 'x', '{SHA}', 's', 'm')",
        f"INSERT INTO chunks (tenant_id, page_id, ord, content, embedding) "
        f"SELECT 'bravo', id, 1, 'c', {VECTOR} FROM page_registry",
        "INSERT INTO audit_log (tenant_id, subject, action, outcome) VALUES ('bravo', 's', 'chat', 'ok')",
        "UPDATE page_registry SET tenant_id = 'bravo'",
        "UPDATE chunks SET tenant_id = 'bravo'",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            run(_exec(app_url, sql, tenant="alpha"))


def test_deleting_or_updating_another_tenants_rows_touches_nothing(db):
    owner_url, app_url = db
    assert run(_exec(app_url, "DELETE FROM page_registry WHERE tenant_id = 'bravo'", tenant="alpha")) == [(0,)]
    assert run(_exec(app_url, "UPDATE chunks SET content = 'x' WHERE tenant_id = 'bravo'", tenant="alpha")) == [(0,)]
    assert run(_exec(owner_url, "SELECT count(*) FROM page_registry WHERE tenant_id = 'bravo'")) == [(1,)]
    assert run(_exec(owner_url, "SELECT count(*) FROM chunks WHERE content = 'x'")) == [(0,)]


def test_deleting_a_page_still_cascades_to_its_chunks(db):
    owner_url, app_url = db
    run(_exec(owner_url, f"WITH p AS (INSERT INTO page_registry (tenant_id, doc_key, page_key, sha256, source_name, "
                         f"markdown) VALUES ('alpha', 'gone', 'p', '{SHA}', 's', 'm') RETURNING id) "
                         f"INSERT INTO chunks (tenant_id, page_id, ord, content, embedding) "
                         f"SELECT 'alpha', id, 0, 'c', {VECTOR} FROM p RETURNING 1"))
    assert run(_exec(app_url, "DELETE FROM page_registry WHERE doc_key = 'gone'", tenant="alpha")) == [(1,)]
    assert run(_exec(owner_url, "SELECT count(*) FROM chunks WHERE content = 'c'")) == [(0,)]


def test_retrieval_with_a_mismatched_session_returns_nothing(db):
    _, app_url = db

    async def sections(bound: str, session_tenant: str) -> list:
        conn = await _connect_as(app_url, bound)
        try:
            return await retrieve(conn, FakeEmbedder(), Session(tenant_id=session_tenant, subject="test"), "alpha")
        finally:
            await conn.close()

    assert [s.markdown for s in run(sections("alpha", "alpha"))] == ["text of alpha"]
    assert run(sections("alpha", "bravo")) == []  # the SQL predicate and the policy disagree: no rows


def test_a_pooled_connection_never_carries_a_tenant_to_its_next_borrower(db):
    """One physical connection (pool of 1) serves alpha, then an unbound borrower, then bravo."""
    _, app_url = db

    async def go() -> dict:
        out: dict = {}
        pool = open_pool(app_url, max_size=1)
        await pool.open(wait=True)
        try:
            async with tenant_transaction(pool, "alpha") as c:
                out["alpha_pid"] = (await (await c.execute("SELECT pg_backend_pid()")).fetchone())[0]
                out["alpha"] = await (await c.execute("SELECT markdown FROM page_registry")).fetchall()
            async with pool.connection() as c:
                out["unbound_pid"] = (await (await c.execute("SELECT pg_backend_pid()")).fetchone())[0]
                out["setting"] = (await (await c.execute("SELECT current_setting('app.tenant_id', true)")).fetchone())[0]
                out["unbound"] = (await (await c.execute("SELECT count(*) FROM page_registry")).fetchone())[0]
            async with tenant_transaction(pool, "bravo") as c:
                out["bravo"] = await (await c.execute("SELECT markdown FROM page_registry")).fetchall()
        finally:
            await pool.close()
        return out

    out = run(go())
    assert out["alpha_pid"] == out["unbound_pid"]  # the same connection was reused
    assert out["alpha"] == [("text of alpha",)] and out["bravo"] == [("text of bravo",)]
    assert out["setting"] in (None, "") and out["unbound"] == 0


def test_an_exhausted_pool_fails_as_database_unavailable(db):
    """Waiting past CONNECT_TIMEOUT_S for a free connection raises OperationalError, which the API maps to 503."""
    _, app_url = db

    async def go() -> None:
        pool = open_pool(app_url, max_size=1)
        await pool.open(wait=True)
        try:
            async with tenant_transaction(pool, "alpha"):
                with pytest.raises(psycopg.OperationalError):
                    async with tenant_transaction(pool, "bravo"):
                        pass
        finally:
            await pool.close()

    run(go())
