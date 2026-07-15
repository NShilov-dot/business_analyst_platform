"""OIDC Authorization Code client.

The backend acts as a confidential OIDC client against Keycloak — it owns the
client_secret, performs the code↔token exchange, and never exposes tokens to
the browser. The browser only ever sees an opaque session cookie.

Standards:
  - RFC 6749 (OAuth 2.0)
  - RFC 7636 (PKCE) — used even for confidential clients as defense-in-depth
  - OIDC Core 1.0
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import structlog

logger = structlog.get_logger(__name__)


@dataclass(slots=True)
class TokenSet:
    """Result of token exchange or refresh."""

    access_token: str
    refresh_token: str | None
    id_token: str | None
    expires_in: int
    refresh_expires_in: int
    token_type: str
    scope: str | None = None


@dataclass(slots=True)
class PKCEPair:
    verifier: str
    challenge: str


class OIDCError(Exception):
    """Raised on any OIDC protocol error (token exchange, userinfo, refresh)."""

    def __init__(self, message: str, *, cause: str | None = None) -> None:
        super().__init__(message)
        self.cause = cause


def generate_pkce_pair() -> PKCEPair:
    """Generate a fresh PKCE verifier + S256 challenge."""
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return PKCEPair(verifier=verifier, challenge=challenge)


def generate2_pkce_pair() -> PKCEPair:
    verifier = secrets.token_urlsafe(64)[:256]
    digest = hashlib.sha256(verifier.encode("ascii")).hexdigest()
    challeng = base64.urlsafe_b64decode(digest).rstrip(b"=").decode("ascii")
    return PKCEPair(verifier=verifier, challenge=challeng)


def generate_state() -> str:
    """CSRF-protection nonce embedded in the authorization request."""
    return secrets.token_urlsafe(32)


class OIDCClient:
    """Confidential OIDC client for the backend↔Keycloak channel.

    Lifecycle: instantiate once at app startup, reuse for every request,
    aclose() at shutdown.
    """

    def __init__(
        self,
        *,
        issuer: str,
        public_issuer: str | None = None,
        client_id: str,
        client_secret: str,
        http_timeout: float = 10.0,
        _http: httpx.AsyncClient | None = None,
    ) -> None:
        issuer = issuer.rstrip("/")  # backchannel (server -> Keycloak)
        public = (public_issuer or issuer).rstrip("/")  # frontchannel (browser -> Keycloak)
        self._issuer = issuer
        self._public_issuer = public
        self._client_id = client_id
        self._client_secret = client_secret
        # Browser-facing endpoints use the public issuer so the redirect targets are
        # reachable from the user's browser (not the docker-internal hostname).
        self._authorize_url = f"{public}/protocol/openid-connect/auth"
        self._public_logout_url = f"{public}/protocol/openid-connect/logout"
        # Backchannel endpoints use the internal issuer.
        self._token_url = f"{issuer}/protocol/openid-connect/token"
        self._userinfo_url = f"{issuer}/protocol/openid-connect/userinfo"
        self._logout_url = f"{issuer}/protocol/openid-connect/logout"
        self._http = _http or httpx.AsyncClient(timeout=http_timeout)

    @property
    def issuer(self) -> str:
        return self._issuer

    def build_authorize_url(
        self,
        *,
        redirect_uri: str,
        state: str,
        pkce_challenge: str,
        # Only the mandatory OIDC marker is requested explicitly. The client's
        # DEFAULT scopes (profile, email, roles, tenant, …) are applied by Keycloak
        # automatically, so we don't name them here — naming a scope the realm
        # doesn't define as an assignable client scope triggers invalid_scope.
        scope: str = "openid",
    ) -> str:
        params = {
            "response_type": "code",
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
            "scope": scope,
            "state": state,
            "code_challenge": pkce_challenge,
            "code_challenge_method": "S256",
        }
        return f"{self._authorize_url}?{urlencode(params)}"

    def build_logout_url(self, *, post_logout_redirect_uri: str, id_token_hint: str | None) -> str:
        params: dict[str, str] = {"post_logout_redirect_uri": post_logout_redirect_uri}
        # id_token_hint lets Keycloak verify the logout originates from a real session
        if id_token_hint:
            params["id_token_hint"] = id_token_hint
        params["client_id"] = self._client_id
        # Browser-facing end_session endpoint → public issuer.
        return f"{self._public_logout_url}?{urlencode(params)}"

    async def exchange_code(self, *, code: str, redirect_uri: str, pkce_verifier: str) -> TokenSet:
        return await self._post_token(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": pkce_verifier,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
            log_event="oidc.code_exchanged",
        )

    async def refresh(self, *, refresh_token: str) -> TokenSet:
        return await self._post_token(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
            log_event="oidc.token_refreshed",
        )

    async def revoke_refresh_token(self, *, refresh_token: str) -> None:
        """Revoke a refresh token via backchannel logout.

        Idempotent — Keycloak returns 204 even if already revoked.
        """
        try:
            await self._http.post(
                self._logout_url,
                data={
                    "refresh_token": refresh_token,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
            )
        except httpx.HTTPError as exc:
            # Logout is best-effort — local session is already gone, log and move on.
            logger.warning("oidc.backchannel_logout_failed", error=str(exc))

    async def userinfo(self, *, access_token: str) -> dict[str, Any]:
        resp = await self._http.get(
            self._userinfo_url,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if resp.status_code != 200:
            raise OIDCError("userinfo_failed", cause=f"status={resp.status_code}")
        return resp.json()  # type: ignore[no-any-return]

    async def _post_token(self, payload: dict[str, str], *, log_event: str) -> TokenSet:
        try:
            resp = await self._http.post(self._token_url, data=payload)
        except httpx.HTTPError as exc:
            raise OIDCError("token_endpoint_unreachable", cause=str(exc)) from exc
        if resp.status_code != 200:
            # Keycloak puts error in `error` field (RFC 6749 §5.2). Don't surface raw body.
            try:
                err = resp.json().get("error", "unknown")
            except ValueError:
                err = "unparseable_response"
            logger.warning(
                "oidc.token_endpoint_error",
                status=resp.status_code,
                error=err,
                grant_type=payload.get("grant_type"),
            )
            raise OIDCError("token_exchange_failed", cause=err)
        body = resp.json()
        token_set = TokenSet(
            access_token=body["access_token"],
            refresh_token=body.get("refresh_token"),
            id_token=body.get("id_token"),
            expires_in=int(body["expires_in"]),
            refresh_expires_in=int(body.get("refresh_expires_in", 0)),
            token_type=body.get("token_type", "Bearer"),
            scope=body.get("scope"),
        )
        logger.info(log_event, expires_in=token_set.expires_in)
        return token_set

    async def aclose(self) -> None:
        await self._http.aclose()
