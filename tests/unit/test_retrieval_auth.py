"""Unit tests for RRF fusion and token → tenant session binding."""

import pytest

from src.retrieval.hybrid import rrf
from src.utils.auth import AuthError, authenticate


def test_rrf_rewards_items_found_by_both_legs():
    scores = rrf([[1, 2, 3], [3, 4]], k=60)
    assert scores[3] == pytest.approx(1 / 63 + 1 / 61)
    assert scores[1] == pytest.approx(1 / 61)
    assert max(scores, key=scores.get) == 3


def test_rrf_empty():
    assert rrf([[], []]) == {}


def test_token_maps_to_its_tenant(monkeypatch):
    monkeypatch.setenv("TENANT_TOKENS", "tokA:acme,tokB:globex")
    assert authenticate("tokB").tenant_id == "globex"


@pytest.mark.parametrize("token", [None, "", "acme", "tokA:acme", "wrong"])
def test_unknown_token_rejected(monkeypatch, token):
    monkeypatch.setenv("TENANT_TOKENS", "tokA:acme")
    with pytest.raises(AuthError):
        authenticate(token)
