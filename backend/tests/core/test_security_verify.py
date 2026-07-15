"""End-to-end verify_token tests with a realistic RS256 Keycloak-shaped token.

This is the test that guards the trust boundary: a real access token (JOSE header
typ "JWT", payload typ "Bearer", signed RS256, matching JWKS) must be ACCEPTED under
the default config, while require_* / typ / leeway behave correctly.
"""

from __future__ import annotations

import base64
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt

from app.core.security import AuthError, JWKSCache, verify_token

_ISSUER = "https://kc/realms/bap"
_AUD = "bap-backend"
_TENANT = "11111111-1111-1111-1111-111111111111"


def _b64u(n: int) -> str:
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _key_and_jwks() -> tuple[str, dict]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    pub = key.public_key().public_numbers()
    jwk = {
        "kty": "RSA",
        "kid": "kid1",
        "use": "sig",
        "alg": "RS256",
        "n": _b64u(pub.n),
        "e": _b64u(pub.e),
    }
    return priv, {"keys": [jwk]}


def _token(priv: str, **claim_overrides: object) -> str:
    now = int(time.time())
    claims: dict[str, object] = {
        "iss": _ISSUER,
        "aud": _AUD,
        "sub": "user-123",
        "exp": now + 300,
        "iat": now,
        "typ": "Bearer",  # the access-token discriminator (PAYLOAD claim)
        "tenant_id": _TENANT,
        "realm_access": {"roles": ["user", "admin"]},
    }
    claims.update(claim_overrides)
    # Keycloak's JOSE header typ is "JWT" — NOT the access-token discriminator.
    return jwt.encode(claims, priv, algorithm="RS256", headers={"kid": "kid1", "typ": "JWT"})


def _jwks_cache(jwks: dict) -> JWKSCache:
    cache = JWKSCache.__new__(JWKSCache)
    cache._issuer = _ISSUER  # type: ignore[attr-defined]
    cache._jwks_uri = f"{_ISSUER}/protocol/openid-connect/certs"  # type: ignore[attr-defined]
    cache._jwks = jwks  # type: ignore[attr-defined]
    cache._expires_at = time.monotonic() + 3600  # type: ignore[attr-defined]
    return cache


async def _verify(token: str, jwks: dict, **kw: object):  # type: ignore[no-untyped-def]
    return await verify_token(
        token,
        jwks=_jwks_cache(jwks),
        audience=_AUD,
        tenant_claim="tenant_id",
        roles_claim="realm_access.roles",
        leeway_seconds=30,
        expected_token_types=frozenset({"Bearer"}),  # the shipped default
        **kw,
    )


@pytest.mark.asyncio
async def test_realistic_keycloak_access_token_is_accepted() -> None:
    """Header typ 'JWT' + payload typ 'Bearer' must pass under the default config."""
    priv, jwks = _key_and_jwks()
    principal = await _verify(_token(priv), jwks)
    assert principal.subject == "user-123"
    assert str(principal.tenant_id) == _TENANT
    assert principal.roles == frozenset({"user", "admin"})


@pytest.mark.asyncio
async def test_recently_expired_token_within_leeway_is_accepted() -> None:
    priv, jwks = _key_and_jwks()
    token = _token(priv, exp=int(time.time()) - 10)  # expired 10s ago, leeway 30s
    principal = await _verify(token, jwks)
    assert principal.subject == "user-123"


@pytest.mark.asyncio
async def test_refresh_typ_payload_is_rejected() -> None:
    priv, jwks = _key_and_jwks()
    with pytest.raises(AuthError) as exc:
        await _verify(_token(priv, typ="Refresh"), jwks)
    assert "unexpected_token_type" in str(exc.value)


@pytest.mark.asyncio
async def test_token_missing_audience_is_rejected() -> None:
    priv, jwks = _key_and_jwks()
    # require_aud: omitting aud must fail rather than silently pass.
    token = _token(priv)
    # Re-sign without aud by overriding to remove it.
    import json

    now = int(time.time())
    claims = {
        "iss": _ISSUER,
        "sub": "u",
        "exp": now + 300,
        "iat": now,
        "typ": "Bearer",
        "tenant_id": _TENANT,
        "realm_access": {"roles": []},
    }
    token = jwt.encode(claims, priv, algorithm="RS256", headers={"kid": "kid1", "typ": "JWT"})
    assert "aud" not in json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))
    with pytest.raises(AuthError):
        await _verify(token, jwks)


@pytest.mark.asyncio
async def test_wrong_signature_is_rejected() -> None:
    priv_a, _ = _key_and_jwks()
    _, jwks_b = _key_and_jwks()  # different key in the JWKS than the signer
    with pytest.raises(AuthError):
        await _verify(_token(priv_a), jwks_b)
