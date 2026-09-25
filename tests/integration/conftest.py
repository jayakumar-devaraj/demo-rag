"""Integration fixtures: a real Postgres+pgvector connection (docker-compose.yml), schema applied."""

import uuid
from collections.abc import Iterator

import pytest

from src.db.connection import apply_schema, connect, run


@pytest.fixture
def tenant() -> str:
    """A fresh tenant id per test, so tests never share rows."""
    return f"t-{uuid.uuid4().hex[:12]}"


@pytest.fixture
def conn() -> Iterator[object]:
    """Yield a live async connection; the schema is applied idempotently first."""
    c = run(connect())
    run(apply_schema(c))
    yield c
    run(c.close())
