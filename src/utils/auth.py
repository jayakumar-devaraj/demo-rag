"""Session authentication: an opaque bearer token maps to exactly one tenant, server-side."""

import hashlib
import hmac
import os

from pydantic import BaseModel, ConfigDict


class AuthError(Exception):
    """Unknown or missing credential (a caller error → 401)."""


class Session(BaseModel):
    """An authenticated caller. tenant_id is only ever derived from the token, never from input."""

    model_config = ConfigDict(frozen=True)
    tenant_id: str
    subject: str  # who acted: a fingerprint of the token, never the token itself


def _token_table() -> dict[str, str]:
    """Parse TENANT_TOKENS ("token:tenant,token:tenant") from the environment."""
    pairs = [p.split(":", 1) for p in os.environ.get("TENANT_TOKENS", "").split(",") if ":" in p]
    return {token.strip(): tenant.strip() for token, tenant in pairs if token.strip() and tenant.strip()}


def authenticate(token: str | None) -> Session:
    """Resolve a bearer token to its Session using a constant-time comparison."""
    if token:
        for known, tenant in _token_table().items():
            if hmac.compare_digest(known.encode(), token.encode()):
                return Session(tenant_id=tenant, subject="token:" + hashlib.sha256(known.encode()).hexdigest()[:16])
    raise AuthError("invalid or missing bearer token")
