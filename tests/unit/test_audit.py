"""Unit tests for the pieces of the audit trail that need no database."""

from src.utils.auth import authenticate
from src.utils.observability import resolve_traceparent

VALID = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"


def test_subject_is_a_stable_token_fingerprint_not_the_token(monkeypatch):
    monkeypatch.setenv("TENANT_TOKENS", "secret-token-a:tenant-a,secret-token-b:tenant-a")
    a1, a2, b = authenticate("secret-token-a"), authenticate("secret-token-a"), authenticate("secret-token-b")
    assert a1.subject == a2.subject != b.subject
    assert a1.subject.startswith("token:") and "secret" not in a1.subject


def test_traceparent_is_kept_only_when_well_formed():
    assert resolve_traceparent(VALID) == VALID
    for bad in (None, "", "garbage", VALID.upper(), VALID + "-extra", "00-" + "0" * 31 + "-" + "1" * 16 + "-01"):
        assert resolve_traceparent(bad) is None
