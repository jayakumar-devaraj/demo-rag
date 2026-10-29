"""Session authentication: a bearer token maps to exactly one tenant, server-side.

Two kinds of token are accepted (ADR 0002):
- a signed JWT from an OIDC provider, when OIDC_ISSUERS, OIDC_AUDIENCE and OIDC_JWKS_URL are set: the
  tenant, subject and scopes come from its claims;
- a static token from TENANT_TOKENS ("token:tenant,..."), during migration only: it carries every
  scope. ALLOW_STATIC_TOKENS=false turns it off.

A token shaped like a JWT is validated as a JWT whenever OIDC is configured; an unreachable key set is
an infrastructure error (503), never a reason to fall back to a static token.
"""

import hashlib
import hmac
import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol

import jwt
from pydantic import BaseModel, ConfigDict

log = logging.getLogger(__name__)

SCOPE_QUERY = "rag:query"
SCOPE_INGEST = "rag:ingest"
ALL_SCOPES = frozenset({SCOPE_QUERY, SCOPE_INGEST})
DEFAULT_ALGORITHMS = ("RS256", "ES256")
JWKS_TIMEOUT_S = 5
JWKS_CACHE_S = 300


class AuthError(Exception):
    """Unknown, malformed, expired or otherwise invalid credential (a caller error → 401)."""


class IdentityProviderUnavailable(Exception):
    """The signing keys could not be fetched (an infrastructure error → 503)."""


class Session(BaseModel):
    """An authenticated caller. tenant_id is only ever derived from the token, never from input."""

    model_config = ConfigDict(frozen=True)
    tenant_id: str
    subject: str  # who acted: the JWT `sub`, or a fingerprint of a static token (never the token)
    scopes: frozenset[str] = frozenset()


class KeyResolver(Protocol):
    """What the validator needs from a JWKS client (PyJWKClient satisfies it)."""

    def get_signing_key_from_jwt(self, token: str) -> Any:
        """Return the key (with a `.key` attribute) that signed `token`."""
        ...


@dataclass(frozen=True)
class OidcConfig:
    """Validation settings taken from the environment."""

    issuers: tuple[str, ...]
    audience: str
    jwks_url: str
    algorithms: tuple[str, ...]
    tenant_claim: str
    leeway_s: int


def oidc_config() -> OidcConfig | None:
    """The OIDC settings, or None when OIDC is not configured; an unsafe algorithm fails loudly."""
    issuers = tuple(i.strip() for i in os.environ.get("OIDC_ISSUERS", "").split(",") if i.strip())
    audience, jwks_url = os.environ.get("OIDC_AUDIENCE", ""), os.environ.get("OIDC_JWKS_URL", "")
    if not (issuers and audience and jwks_url):
        return None
    raw = os.environ.get("OIDC_ALGORITHMS", "")
    algorithms = tuple(a.strip() for a in raw.split(",") if a.strip()) or DEFAULT_ALGORITHMS
    unsafe = [a for a in algorithms if a.lower() == "none" or a.upper().startswith("HS")]
    if unsafe:
        raise RuntimeError(f"OIDC_ALGORITHMS must be asymmetric; refusing {unsafe}")
    return OidcConfig(
        issuers=issuers,
        audience=audience,
        jwks_url=jwks_url,
        algorithms=algorithms,
        tenant_claim=os.environ.get("OIDC_TENANT_CLAIM", "tenant"),
        leeway_s=int(os.environ.get("OIDC_CLOCK_SKEW_S", "30")),
    )


@lru_cache(maxsize=4)
def _jwks_client(url: str) -> jwt.PyJWKClient:
    """One caching key client per JWKS URL (keys are cached in memory for JWKS_CACHE_S)."""
    return jwt.PyJWKClient(url, cache_keys=True, lifespan=JWKS_CACHE_S, timeout=JWKS_TIMEOUT_S)


def _scopes(claims: dict[str, Any]) -> frozenset[str]:
    """Scopes from `scope` (space separated) or `scp` (list or space separated)."""
    raw = claims.get("scope", claims.get("scp", ""))
    return frozenset(raw.split() if isinstance(raw, str) else [str(s) for s in raw])


def session_from_jwt(token: str, cfg: OidcConfig, keys: KeyResolver | None = None) -> Session:
    """Validate a JWT (signature, algorithm, issuer, audience, expiry) and build the Session."""
    try:
        signing_key = (keys or _jwks_client(cfg.jwks_url)).get_signing_key_from_jwt(token)
    except jwt.PyJWKClientConnectionError as exc:
        log.error("idp_unavailable", extra={"error": type(exc).__name__})
        raise IdentityProviderUnavailable("identity provider unavailable") from exc
    except jwt.PyJWTError as exc:
        raise AuthError("invalid or missing bearer token") from exc
    try:
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=list(cfg.algorithms),
            audience=cfg.audience,
            issuer=list(cfg.issuers),
            leeway=cfg.leeway_s,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise AuthError("invalid or missing bearer token") from exc
    tenant = claims.get(cfg.tenant_claim)
    if not isinstance(tenant, str) or not tenant.strip():
        raise AuthError("token has no tenant")
    return Session(tenant_id=tenant, subject=str(claims["sub"]), scopes=_scopes(claims))


def _token_table() -> dict[str, str]:
    """Parse TENANT_TOKENS ("token:tenant,token:tenant") from the environment."""
    pairs = [p.split(":", 1) for p in os.environ.get("TENANT_TOKENS", "").split(",") if ":" in p]
    return {token.strip(): tenant.strip() for token, tenant in pairs if token.strip() and tenant.strip()}


def static_tokens_allowed() -> bool:
    """Static tokens are accepted unless ALLOW_STATIC_TOKENS is set to false."""
    return os.environ.get("ALLOW_STATIC_TOKENS", "true").strip().lower() != "false"


def authenticate(token: str | None, keys: KeyResolver | None = None) -> Session:
    """Resolve a bearer token to its Session; static tokens use a constant-time comparison."""
    if token:
        cfg = oidc_config()
        if cfg is not None and token.count(".") == 2:
            return session_from_jwt(token, cfg, keys)
        if static_tokens_allowed():
            for known, tenant in _token_table().items():
                if hmac.compare_digest(known.encode(), token.encode()):
                    fingerprint = hashlib.sha256(known.encode()).hexdigest()[:16]
                    return Session(tenant_id=tenant, subject=f"token:{fingerprint}", scopes=ALL_SCOPES)
    raise AuthError("invalid or missing bearer token")
