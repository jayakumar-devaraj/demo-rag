"""Alembic environment: migrations run as the schema owner, then grants are (re)applied to the app role."""

import os

from alembic import context
from sqlalchemy import create_engine, pool

# The API connects as a non-owner role. Owner-only DDL stays in migrations; this grants what the API
# needs, and nothing more on audit_log (append-only: no UPDATE, DELETE or TRUNCATE).
GRANTS_SQL = """
GRANT USAGE ON SCHEMA public TO {role};
GRANT SELECT, INSERT, UPDATE, DELETE ON page_registry, chunks TO {role};
REVOKE ALL ON audit_log FROM {role};
GRANT SELECT, INSERT ON audit_log TO {role};
GRANT SELECT ON alembic_version TO {role};
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {role};
"""


def migration_url() -> str:
    """Return MIGRATION_DATABASE_URL in SQLAlchemy's psycopg3 form, failing loudly when it is unset."""
    url = os.environ.get("MIGRATION_DATABASE_URL")
    if not url:
        raise RuntimeError("MIGRATION_DATABASE_URL is not set (the schema owner's connection string)")
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


engine = create_engine(migration_url(), poolclass=pool.NullPool)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()
        role = os.environ.get("APP_DB_ROLE")
        if role:
            quoted = connection.dialect.identifier_preparer.quote(role)
            connection.exec_driver_sql(GRANTS_SQL.format(role=quoted))
