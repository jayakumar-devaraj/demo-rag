"""Baseline: the schema that src/db/schema.sql provisioned before migrations existed.

Every statement is idempotent, so a database created by the old init script adopts this revision
by running `alembic upgrade head` with no data change.

Revision ID: 0001
Revises:
"""
from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = Path(__file__).resolve().parents[1].joinpath("baseline_schema.sql").read_text(encoding="utf-8")
    op.get_bind().exec_driver_sql(sql)
