"""Unit tests for OIDCClient.

Uses an httpx.AsyncBaseTransport fake — no real Keycloak required.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.core.oidc import (
    OIDCClient,
    OIDCError,
    generate_pkce_pair,
    generate_state,
)

_ISSUER = "http://kc:8080/realms/saas"


@dataclass
class _Route:
    method: str
    url_part: str
    status: int
    json: dict | None = None
    text: str = ""
    received_payloads: list[dict] = field(default_factory=list)


class _FakeTransport(httpx.AsyncBaseTransport):
    def __init__(self, *routes: _Route) -> None:
        self._routes = list(routes)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for route in self._routes:
            if request.method == route.method and route.url_part in url:
                if request.content:
                    # capture form-encoded payload for assertions
                    body = request.content.decode()
                    route.received_payloads.append({k: v[0] for k, v in parse_qs(body).items()})
                if route.json is not None:
                    return httpx.Response(route.status, json=route.json)
                return httpx.Response(route.status, text=route.text)
        raise AssertionError(f"No route matched: {request.method} {url!r}")


def _make_client(*routes: _Route) -> OIDCClient:
    return OIDCClient(
        issuer=_ISSUER,
        client_id="saas-backend",
        client_secret="topsecret",
        _http=httpx.AsyncClient(transport=_FakeTransport(*routes)),
    )


def test_public_issuer_used_for_browser_endpoints_internal_for_backchannel() -> None:
    """authorize + end_session target the public issuer; token/userinfo the internal one."""
    client = OIDCClient(
        issuer="http://keycloak:8080/realms/saas",  # backchannel
        public_issuer="http://localhost:8080/realms/saas",  # browser
        client_id="saas-backend",
        client_secret="topsecret",
    )
    authorize = client.build_authorize_url(
        redirect_uri="http://localhost:3000/v1/auth/callback",
        state="S",
        pkce_challenge="C",
    )
    logout = client.build_logout_url(
        post_logout_redirect_uri="http://localhost:3000/", id_token_hint="idt"
    )
    assert authorize.startswith("http://localhost:8080/realms/saas/protocol/openid-connect/auth")
    assert logout.startswith("http://localhost:8080/realms/saas/protocol/openid-connect/logout")
    # Backchannel endpoints stay on the internal hostname.
    assert client._token_url.startswith("http://keycloak:8080/")  # type: ignore[attr-defined]
    assert client._userinfo_url.startswith("http://keycloak:8080/")  # type: ignore[attr-defined]


def test_public_issuer_defaults_to_internal_when_unset() -> None:
    client = OIDCClient(issuer=_ISSUER, client_id="saas-backend", client_secret="x")
    url = client.build_authorize_url(redirect_uri="http://cb", state="S", pkce_challenge="C")
    assert url.startswith(f"{_ISSUER}/protocol/openid-connect/auth")


# ---------------------------------------------------------------------------
# PKCE / state helpers
# ---------------------------------------------------------------------------


def test_pkce_pair_uses_s256_challenge() -> None:
    pair = generate_pkce_pair()
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(pair.verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    assert pair.challenge == expected


def test_pkce_verifier_within_rfc7636_bounds() -> None:
    pair = generate_pkce_pair()
    assert 43 <= len(pair.verifier) <= 128


def test_state_is_url_safe_and_nontrivial() -> None:
    s1 = generate_state()
    s2 = generate_state()
    assert s1 != s2
    assert len(s1) >= 32


# ---------------------------------------------------------------------------
# Authorize URL construction
# ---------------------------------------------------------------------------


def test_authorize_url_contains_required_params() -> None:
    client = _make_client()
    url = client.build_authorize_url(
        redirect_uri="http://localhost:8000/v1/auth/callback",
        state="STATE123",
        pkce_challenge="CHALLENGE",
    )
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    assert qs["response_type"] == ["code"]
    assert qs["client_id"] == ["saas-backend"]
    assert qs["state"] == ["STATE123"]
    assert qs["code_challenge"] == ["CHALLENGE"]
    assert qs["code_challenge_method"] == ["S256"]
    assert qs["redirect_uri"] == ["http://localhost:8000/v1/auth/callback"]
    assert parsed.path.endswith("/openid-connect/auth")


# ---------------------------------------------------------------------------
# Token exchange
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exchange_code_returns_tokenset() -> None:
    route = _Route(
        "POST",
        "/openid-connect/token",
        200,
        json={
            "access_token": "AT",
            "refresh_token": "RT",
            "id_token": "IT",
            "expires_in": 300,
            "refresh_expires_in": 1800,
            "token_type": "Bearer",
        },
    )
    client = _make_client(route)
    result = await client.exchange_code(
        code="abc",
        redirect_uri="http://localhost:8000/v1/auth/callback",
        pkce_verifier="VERIFIER",
    )
    assert result.access_token == "AT"
    assert result.refresh_token == "RT"
    assert result.expires_in == 300

    payload = route.received_payloads[0]
    assert payload["grant_type"] == "authorization_code"
    assert payload["code"] == "abc"
    assert payload["code_verifier"] == "VERIFIER"
    assert payload["client_secret"] == "topsecret"


@pytest.mark.asyncio
async def test_exchange_code_raises_on_error() -> None:
    route = _Route(
        "POST",
        "/openid-connect/token",
        400,
        json={"error": "invalid_grant"},
    )
    client = _make_client(route)
    with pytest.raises(OIDCError) as exc_info:
        await client.exchange_code(code="bad", redirect_uri="http://x/cb", pkce_verifier="V")
    assert exc_info.value.cause == "invalid_grant"


@pytest.mark.asyncio
async def test_refresh_uses_refresh_token_grant() -> None:
    route = _Route(
        "POST",
        "/openid-connect/token",
        200,
        json={
            "access_token": "AT2",
            "refresh_token": "RT2",
            "expires_in": 600,
        },
    )
    client = _make_client(route)
    result = await client.refresh(refresh_token="old-RT")
    assert result.access_token == "AT2"
    payload = route.received_payloads[0]
    assert payload["grant_type"] == "refresh_token"
    assert payload["refresh_token"] == "old-RT"


@pytest.mark.asyncio
async def test_refresh_raises_on_revoked_token() -> None:
    route = _Route(
        "POST",
        "/openid-connect/token",
        400,
        json={"error": "invalid_grant"},
    )
    client = _make_client(route)
    with pytest.raises(OIDCError):
        await client.refresh(refresh_token="revoked")


@pytest.mark.asyncio
async def test_revoke_refresh_token_is_idempotent_on_failure() -> None:
    # Even if Keycloak returns a 500, revoke must not raise.
    route = _Route("POST", "/openid-connect/logout", 500, text="oops")
    client = _make_client(route)
    await client.revoke_refresh_token(refresh_token="any")  # must not raise


@pytest.mark.asyncio
async def test_userinfo() -> None:
    route = _Route(
        "GET",
        "/openid-connect/userinfo",
        200,
        json={"sub": "u1", "email": "a@b"},
    )
    client = _make_client(route)
    info = await client.userinfo(access_token="AT")
    assert info["sub"] == "u1"
