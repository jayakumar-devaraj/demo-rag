"""The revision the API expects at startup must be the Alembic head."""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from src.db.connection import EXPECTED_SCHEMA_REVISION

ROOT = Path(__file__).resolve().parents[2]


def test_expected_revision_is_the_alembic_head():
    script = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    assert script.get_heads() == [EXPECTED_SCHEMA_REVISION]
