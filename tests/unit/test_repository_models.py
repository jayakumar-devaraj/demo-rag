"""Unit tests for repository input validation (no database)."""

import pytest
from pydantic import ValidationError

from src.db.connection import EMBEDDING_DIM
from src.db.repository import ChunkIn, PageIn


def test_page_rejects_non_sha256_hash():
    with pytest.raises(ValidationError):
        PageIn(tenant_id="t", doc_key="d", page_key="p", sha256="abc", source_name="s", markdown="m")


def test_page_rejects_empty_tenant():
    with pytest.raises(ValidationError):
        PageIn(tenant_id="", doc_key="d", page_key="p", sha256="0" * 64, source_name="s", markdown="m")


@pytest.mark.parametrize("dim", [EMBEDDING_DIM - 1, EMBEDDING_DIM + 1])
def test_chunk_rejects_wrong_embedding_dim(dim):
    with pytest.raises(ValidationError):
        ChunkIn(content="x", embedding=[0.0] * dim)
