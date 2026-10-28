"""Row-level security: the database itself confines every query to the tenant bound on the connection.

The API sets `app.tenant_id` on its connection (src/db/connection.py). With the setting absent the
policies compare against NULL, so a connection that forgot to bind a tenant sees no rows. FORCE makes
the policies apply to the table owner too (superusers and roles with BYPASSRLS still bypass them, so
the API role must never have either).

Revision ID: 0003
Revises: 0002
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

TENANT = "tenant_id = current_setting('app.tenant_id', true)"


def upgrade() -> None:
    sql = []
    for table in ("page_registry", "chunks"):
        sql += [
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
            f"DROP POLICY IF EXISTS tenant_isolation ON {table}",
            f"CREATE POLICY tenant_isolation ON {table} USING ({TENANT}) WITH CHECK ({TENANT})",
        ]
    sql += [
        "ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE audit_log FORCE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS tenant_read ON audit_log",
        "DROP POLICY IF EXISTS tenant_insert ON audit_log",
        f"CREATE POLICY tenant_read ON audit_log FOR SELECT USING ({TENANT})",
        f"CREATE POLICY tenant_insert ON audit_log FOR INSERT WITH CHECK ({TENANT})",
    ]
    bind = op.get_bind()
    for statement in sql:
        bind.exec_driver_sql(statement)
