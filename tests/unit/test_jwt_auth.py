"""JWT tenant authentication (ADR 0002): claims, rejection cases, scopes, and the no-fallback rule.

Keys are generated here; one test serves them from a real local JWKS endpoint over HTTP.
"""

import base64
import hashlib
import hmac
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from src.routers.api import Services, create_app
from src.utils import auth
from src.utils.auth import AuthError, IdentityProviderUnavailable, authenticate
from tests.conftest import FakeEmbedder, ScriptedLLM

ISSUER, AUDIENCE = "https://idp.example/realms/rag", "rag-api"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeKeys:
    """TEST DOUBLE: a JWKS client that returns one fixed public key, or fails like an unreachable IdP."""

    def __init__(self, key=KEY, error: Exception | None = None) -> None:
        self.key, self.error = key, error

    def get_signing_key_from_jwt(self, token):
        if self.error:
            raise self.error
        return SimpleNamespace(key=self.key.public_key())


def token(key=KEY, alg="RS256", **claims) -> str:
    now = int(time.time())
    body = {"iss": ISSUER, "aud": AUDIENCE, "sub": "svc-chatbot", "tenant": "tenant-a",
            "scope": "rag:query", "iat": now, "exp": now + 300} | claims
    return jwt.encode({k: v for k, v in body.items() if v is not None}, key, algorithm=alg,
                      headers={"kid": "k1"})


@pytest.fixture(autouse=True)
def oidc_env(monkeypatch):
    monkeypatch.setenv("OIDC_ISSUERS", ISSUER)
    monkeypatch.setenv("OIDC_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("OIDC_JWKS_URL", "http://127.0.0.1:1/jwks")
    monkeypatch.setenv("TENANT_TOKENS", "static-token:tenant-s")


def test_valid_token_yields_tenant_subject_and_scopes():
    s = authenticate(token(scope="rag:query rag:ingest"), FakeKeys())
    assert (s.tenant_id, s.subject) == ("tenant-a", "svc-chatbot")
    assert s.scopes == {"rag:query", "rag:ingest"}


def test_scopes_may_arrive_as_a_scp_list():
    s = authenticate(token(scope=None, scp=["rag:ingest"]), FakeKeys())
    assert s.scopes == {"rag:ingest"}


@pytest.mark.parametrize("claims", [
    {"iss": "https://evil.example"}, {"aud": "someone-else"}, {"exp": int(time.time()) - 3600},
    {"sub": None}, {"tenant": None}, {"tenant": "  "}, {"tenant": 7},
])
def test_invalid_claims_are_rejected(claims):
    with pytest.raises(AuthError):
        authenticate(token(**claims), FakeKeys())


def test_a_token_signed_by_another_key_is_rejected():
    with pytest.raises(AuthError):
        authenticate(token(key=OTHER_KEY), FakeKeys())


def test_unsigned_and_symmetric_tokens_are_rejected():
    unsigned = ".".join(base64.urlsafe_b64encode(json.dumps(p).encode()).rstrip(b"=").decode()
                        for p in ({"alg": "none", "typ": "JWT"}, {"iss": ISSUER, "aud": AUDIENCE,
                                  "sub": "x", "tenant": "t", "exp": int(time.time()) + 300})) + "."
    with pytest.raises(AuthError):
        authenticate(unsigned, FakeKeys())
    public_pem = KEY.public_key().public_bytes(
        encoding=serialization.Encoding.PEM, format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    signing_input = ".".join(
        base64.urlsafe_b64encode(json.dumps(p).encode()).rstrip(b"=").decode()
        for p in ({"alg": "HS256", "typ": "JWT"}, {"iss": ISSUER, "aud": AUDIENCE, "sub": "x",
                  "tenant": "t", "exp": int(time.time()) + 300}))
    mac = hmac.new(public_pem, signing_input.encode(), hashlib.sha256).digest()
    forged = signing_input + "." + base64.urlsafe_b64encode(mac).rstrip(b"=").decode()
    with pytest.raises(AuthError):  # algorithm confusion: HS256 "signed" with the public key
        authenticate(forged, FakeKeys())


@pytest.mark.parametrize("bad", ["HS256", "none", "hs512"])
def test_symmetric_algorithms_cannot_be_configured(monkeypatch, bad):
    monkeypatch.setenv("OIDC_ALGORITHMS", f"RS256,{bad}")
    with pytest.raises(RuntimeError, match="asymmetric"):
        auth.oidc_config()


def test_unreachable_keys_are_503_material_and_never_fall_back_to_static():
    err = jwt.PyJWKClientConnectionError("down")
    with pytest.raises(IdentityProviderUnavailable):
        authenticate(token(), FakeKeys(error=err))
    assert authenticate("static-token").tenant_id == "tenant-s"  # not JWT-shaped: static path unchanged


def test_static_tokens_carry_all_scopes_and_can_be_switched_off(monkeypatch):
    assert authenticate("static-token").scopes == auth.ALL_SCOPES
    monkeypatch.setenv("ALLOW_STATIC_TOKENS", "false")
    with pytest.raises(AuthError):
        authenticate("static-token")


def test_without_oidc_configured_only_static_tokens_work(monkeypatch):
    monkeypatch.delenv("OIDC_ISSUERS")
    assert auth.oidc_config() is None
    with pytest.raises(AuthError):
        authenticate(token(), FakeKeys())


def _client(monkeypatch, keys) -> TestClient:
    monkeypatch.setattr(auth, "_jwks_client", lambda url: keys)
    return TestClient(create_app(Services(embedder=FakeEmbedder(), llm=ScriptedLLM(), triad=None)))


def test_api_maps_auth_failures_to_401_403_and_503(monkeypatch):
    bearer = lambda t: {"Authorization": f"Bearer {t}"}  # noqa: E731
    c = _client(monkeypatch, FakeKeys())
    assert c.post("/v1/retrieve", json={"question": "q"}, headers=bearer("junk.jwt.shape")).status_code == 401
    # a query-only token may not ingest; an ingest-only token may not query
    assert c.post("/ingest", files={"file": ("a.txt", b"x")}, headers=bearer(token())).status_code == 403
    assert c.post("/v1/retrieve", json={"question": "q"},
                  headers=bearer(token(scope="rag:ingest"))).status_code == 403
    c = _client(monkeypatch, FakeKeys(error=jwt.PyJWKClientConnectionError("down")))
    r = c.post("/v1/retrieve", json={"question": "q"}, headers=bearer(token()))
    assert r.status_code == 503 and r.json()["detail"] == "identity provider unavailable"


class _Jwks(BaseHTTPRequestHandler):
    body = b""

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *args):  # silence test output
        pass


def test_keys_are_fetched_from_a_real_jwks_endpoint(monkeypatch):
    jwk = json.loads(RSAAlgorithm.to_jwk(KEY.public_key()))
    jwk.update(kid="k1", use="sig", alg="RS256")
    _Jwks.body = json.dumps({"keys": [jwk]}).encode()
    server = HTTPServer(("127.0.0.1", 0), _Jwks)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("OIDC_JWKS_URL", f"http://127.0.0.1:{server.server_port}/jwks")
        auth._jwks_client.cache_clear()
        assert authenticate(token()).tenant_id == "tenant-a"
        with pytest.raises(AuthError):  # signed by a key the endpoint does not publish
            authenticate(token(key=OTHER_KEY))
    finally:
        server.shutdown()
    auth._jwks_client.cache_clear()
    monkeypatch.setenv("OIDC_JWKS_URL", f"http://127.0.0.1:{server.server_port}/jwks")  # now closed
    with pytest.raises(IdentityProviderUnavailable):
        authenticate(token())
