from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import httpx
import structlog
from jose import jwt
from jose.exceptions import JWTError

from app.core.errors import DomainError

logger = structlog.get_logger(__name__)

# Whitelist of signing algorithms accepted from Keycloak JWKS.
# Excludes 'none', all HMAC variants (HS*), and legacy weak algorithms.
_ALLOWED_ALGORITHMS = frozenset(
    {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "PS256", "PS384", "PS512"}
)


class AuthError(DomainError):
    code = "AUTH_FAILED"
    http_status = 401


@dataclass(frozen=True, slots=True)
class Principal:
    subject: str
    tenant_id: UUID
    roles: frozenset[str]
    raw_claims: dict[str, Any] = field(hash=False, compare=False, default_factory=dict)


class JWKSCache:
    """Fetch JWKS from Keycloak and cache it for `ttl_seconds` (per process)."""

    def __init__(
        self, *, issuer: str, jwks_uri: str | None = None, ttl_seconds: int = 3600
    ) -> None:
        # `issuer` is the EXPECTED token `iss` (the public/frontchannel value Keycloak
        # stamps into tokens). `jwks_uri` is where to FETCH the keys from — the internal
        # backchannel URL when those differ (Keycloak behind docker/ingress). They
        # coincide in single-URL deployments.
        self._issuer = issuer.rstrip("/")
        self._jwks_uri = jwks_uri or f"{self._issuer}/protocol/openid-connect/certs"
        self._ttl = ttl_seconds
        self._jwks: dict[str, Any] | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()
        self._client = httpx.AsyncClient(timeout=5.0)

    @property
    def issuer(self) -> str:
        return self._issuer

    async def get(self, *, force_refresh: bool = False) -> dict[str, Any]:
        if not force_refresh and self._jwks is not None and time.monotonic() < self._expires_at:
            return self._jwks
        async with self._lock:
            if not force_refresh and self._jwks is not None and time.monotonic() < self._expires_at:
                return self._jwks
            response = await self._client.get(self._jwks_uri)
            response.raise_for_status()
            self._jwks = response.json()
            self._expires_at = time.monotonic() + self._ttl
            logger.info("jwks.refreshed", issuer=self._issuer, key_count=len(self._jwks["keys"]))
            return self._jwks

    async def aclose(self) -> None:
        await self._client.aclose()


def _find_key(jwks: dict[str, Any], kid: str) -> dict[str, Any] | None:
    return next((k for k in jwks["keys"] if k.get("kid") == kid), None)


def _get_path(claims: dict[str, Any], path: str) -> Any:
    cursor: Any = claims
    for part in path.split("."):
        if not isinstance(cursor, dict) or part not in cursor:
            return None
        cursor = cursor[part]
    return cursor


async def verify_token(
    token: str,
    *,
    jwks: JWKSCache,
    audience: str,
    tenant_claim: str,
    roles_claim: str,
    leeway_seconds: int,
    expected_token_types: frozenset[str] = frozenset(),
) -> Principal:
    """Verify a Keycloak access token and produce a Principal.

    Refreshes JWKS once on `kid` miss to handle realm key rotation without restart.
    All verification failures are logged at WARNING level for audit purposes.

    `expected_token_types` is matched against the `typ` PAYLOAD claim (Keycloak emits
    "Bearer" for access tokens, "ID"/"Refresh"/"Logout" for the others; the JOSE
    *header* typ is always "JWT" and cannot distinguish them). This is the one trust
    boundary asserting "this is an access token", rejecting an id/refresh/logout token
    presented in its place. Checked after signature verification. Empty set disables it.
    """

    def _fail(reason: str) -> AuthError:
        logger.warning("auth.token_rejected", reason=reason)
        return AuthError(reason)

    try:
        unverified_header = jwt.get_unverified_header(token)
    except JWTError as exc:
        raise _fail("malformed_bearer_token") from exc

    kid = unverified_header.get("kid")
    if kid is None:
        raise _fail("token_header_missing_kid")

    keys = await jwks.get()
    key = _find_key(keys, kid)
    if key is None:
        keys = await jwks.get(force_refresh=True)
        key = _find_key(keys, kid)
    if key is None:
        raise _fail("signing_key_not_found")

    # Enforce algorithm allowlist — reject 'none', HMAC, and unknown algorithms
    # regardless of what the JWKS key or token header claims.
    alg = key.get("alg", "RS256")
    if alg not in _ALLOWED_ALGORITHMS:
        raise _fail(f"disallowed_signing_algorithm:{alg}")

    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=[alg],
            audience=audience,
            issuer=jwks.issuer,
            options={
                "verify_at_hash": False,
                # Reject tokens that simply omit the claims we rely on, rather than
                # silently passing validation when aud/exp/iss/sub are absent.
                "require_aud": True,
                "require_exp": True,
                "require_iss": True,
                "require_sub": True,
                # python-jose takes leeway via options, not a kwarg.
                "leeway": leeway_seconds,
            },
        )
    except JWTError as exc:
        # Log the jose detail (generic message, e.g. "Signature verification failed" /
        # "Invalid audience") to aid debugging; keep the client-facing reason generic.
        logger.warning("auth.token_rejected", reason="token_decode_failed", detail=str(exc))
        raise AuthError("token_decode_failed") from exc

    # Token-type check on the verified PAYLOAD claim (not the JOSE header).
    if expected_token_types and claims.get("typ") not in expected_token_types:
        raise _fail(f"unexpected_token_type:{claims.get('typ')}")

    subject = claims.get("sub")
    if not isinstance(subject, str):
        raise _fail("token_missing_sub")

    tenant_raw = _get_path(claims, tenant_claim)
    if not isinstance(tenant_raw, str):
        raise _fail(f"token_missing_claim:{tenant_claim}")
    try:
        tenant_id = UUID(tenant_raw)
    except ValueError as exc:
        raise _fail(f"tenant_claim_not_uuid:{tenant_claim}") from exc

    roles_raw = _get_path(claims, roles_claim)
    # Only a list yields roles; a string/dict claim must not be iterated into
    # character/key "roles" (fail closed — grant nothing on a malformed claim).
    if not isinstance(roles_raw, list):
        roles_raw = []
    roles = frozenset(r for r in roles_raw if isinstance(r, str))

    logger.debug("auth.token_verified", subject=subject, tenant_id=str(tenant_id))
    return Principal(subject=subject, tenant_id=tenant_id, roles=roles, raw_claims=claims)
