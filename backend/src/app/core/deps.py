from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    # Typing-only: keeps this module framework/module-boundary clean at
    # runtime (core must not import from `modules/*`) while still giving the
    # accessor a real type in editors/mypy.
    from app.modules.documents.domain.ports import ObjectStorePort

from app.config import Settings, get_settings
from app.core.errors import (
    CsrfError,
    PermissionDeniedError,
    RateLimitError,
    ServiceUnavailableError,
)
from app.core.keycloak_admin import KeycloakAdminClient
from app.core.oidc import OIDCClient, OIDCError
from app.core.rate_limit import RateLimiterDep
from app.core.security import AuthError, JWKSCache, Principal, verify_token
from app.core.sessions import SessionData, SessionStore
from app.core.tenancy import TenantContext, resolve_tenant, session_for_tenant

# ---------------------------------------------------------------------------
# Settings / JWKS / OIDC / SessionStore providers
# ---------------------------------------------------------------------------


def _settings_dep() -> Settings:
    return get_settings()


SettingsDep = Annotated[Settings, Depends(_settings_dep)]


def _jwks_dep(request: Request) -> JWKSCache:
    jwks: JWKSCache = request.app.state.jwks
    return jwks


JWKSDep = Annotated[JWKSCache, Depends(_jwks_dep)]


def _oidc_dep(request: Request) -> OIDCClient:
    oidc: OIDCClient = request.app.state.oidc
    return oidc


OIDCDep = Annotated[OIDCClient, Depends(_oidc_dep)]


def _session_store_dep(request: Request) -> SessionStore:
    store: SessionStore = request.app.state.session_store
    return store


SessionStoreDep = Annotated[SessionStore, Depends(_session_store_dep)]


# ---------------------------------------------------------------------------
# Principal — derived from server-side session cookie (BFF model)
#
# Order of operations:
#   1. Read session_id from HttpOnly cookie. Missing → 401.
#   2. Look up SessionData in Redis.       Missing → 401 + clear cookie.
#   3. If access_token within 30s of expiry → refresh at Keycloak,
#      persist new tokens. Refresh failure → 401 + clear session.
#   4. Verify access_token signature against cached JWKS, derive Principal.
#   5. Touch session (slide idle TTL forward).
#
# The browser never sees a JWT. Sessions live entirely server-side in Redis.
# ---------------------------------------------------------------------------

_REFRESH_LEEWAY_SECONDS = 30
# When we lose the single-flight lock, poll the store for the winner's rotated
# tokens: up to _REFRESH_WAIT_POLLS * _REFRESH_WAIT_INTERVAL (~1s) before giving up.
_REFRESH_WAIT_POLLS = 20
_REFRESH_WAIT_INTERVAL = 0.05


async def _await_concurrent_refresh(
    store: SessionStore, sid: str, data: SessionData
) -> SessionData | None:
    """Wait briefly for a concurrent lock-holder to store refreshed tokens.

    Returns the refreshed SessionData once its access_token differs from ours,
    or None if the session vanished or no refresh was observed within the window.
    """
    for _ in range(_REFRESH_WAIT_POLLS):
        await asyncio.sleep(_REFRESH_WAIT_INTERVAL)
        latest = await store.get(sid)
        if latest is None:
            return None
        if latest.access_token != data.access_token:
            return latest
    return None


async def _maybe_refresh(
    sid: str,
    data: SessionData,
    *,
    oidc: OIDCClient,
    store: SessionStore,
) -> SessionData:
    """Refresh access_token if it expires soon; return possibly-updated data.

    Single-flight: only ONE concurrent request refreshes; the others wait for its
    rotated tokens. Without this, two requests in the 30s pre-expiry window (the
    SPA routinely fans out several at once) both refresh with the same one-time
    refresh_token — Keycloak rotates on the first, the second fails, and the old
    behavior then DELETED the live session, force-logging the user out mid-flow.
    """
    if data.access_expires_at - _REFRESH_LEEWAY_SECONDS > int(time.time()):
        return data
    if data.refresh_token is None:
        # Nothing to refresh with — caller falls through to verify (which fails on
        # an expired token) and surfaces AuthError.
        return data

    if not await store.acquire_refresh_lock(sid):
        # Someone else is refreshing. Reuse their rotated tokens once available;
        # otherwise fall back to our still-within-leeway token (verify may pass).
        refreshed = await _await_concurrent_refresh(store, sid, data)
        return refreshed if refreshed is not None else data

    try:
        try:
            tokens = await oidc.refresh(refresh_token=data.refresh_token)
        except OIDCError:
            # Our refresh failed. A racing request may already have rotated and
            # stored fresh tokens — prefer those over destroying the session.
            latest = await store.get(sid)
            if latest is not None and latest.access_token != data.access_token:
                return latest
            await store.delete(sid)
            raise AuthError("Session expired") from None

        updated = SessionData(
            subject=data.subject,
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token or data.refresh_token,
            id_token=tokens.id_token or data.id_token,
            access_expires_at=int(time.time()) + tokens.expires_in,
            created_at=data.created_at,
        )
        await store.update(sid, updated)
        return updated
    finally:
        await store.release_refresh_lock(sid)


async def _principal(
    request: Request,
    settings: SettingsDep,
    jwks: JWKSDep,
    oidc: OIDCDep,
    store: SessionStoreDep,
) -> Principal:
    sid = request.cookies.get(settings.session_cookie_effective_name)
    if not sid:
        raise AuthError("Not authenticated")

    data = await store.get(sid)
    if data is None:
        raise AuthError("Session expired")

    data = await _maybe_refresh(sid, data, oidc=oidc, store=store)

    async def _verify(token: str) -> Principal:
        return await verify_token(
            token,
            jwks=jwks,
            audience=settings.keycloak_audience,
            tenant_claim=settings.keycloak_tenant_claim,
            roles_claim=settings.keycloak_roles_claim,
            leeway_seconds=settings.keycloak_leeway_seconds,
            expected_token_types=frozenset(settings.keycloak_expected_token_types),
        )

    try:
        principal = await _verify(data.access_token)
    except AuthError:
        # Our token failed verification. A concurrent request may have refreshed
        # the session while we held a stale token (e.g. a single-flight loser that
        # timed out waiting). Re-read once; if a newer token is now stored, verify
        # THAT before giving up — otherwise we'd delete a session another request
        # just refreshed, reintroducing the force-logout race in a new path.
        latest = await store.get(sid)
        if latest is not None and latest.access_token != data.access_token:
            try:
                principal = await _verify(latest.access_token)
            except AuthError:
                await store.delete(sid)
                raise
            await store.touch(sid)
            return principal
        # Session-backed token genuinely failed verification — kill the session.
        await store.delete(sid)
        raise

    # Slide the idle window — every authenticated request keeps the session warm.
    await store.touch(sid)
    return principal


PrincipalDep = Annotated[Principal, Depends(_principal)]


# ---------------------------------------------------------------------------
# Tenant + DB session
# ---------------------------------------------------------------------------


async def _tenant(principal: PrincipalDep) -> TenantContext:
    return await resolve_tenant(tenant_id=principal.tenant_id)


TenantDep = Annotated[TenantContext, Depends(_tenant)]


async def _session(tenant: TenantDep) -> AsyncIterator[AsyncSession]:
    async for session in session_for_tenant(tenant):
        yield session


SessionDep = Annotated[AsyncSession, Depends(_session)]


# ---------------------------------------------------------------------------
# RBAC helper
# ---------------------------------------------------------------------------


def require_roles(*roles: str) -> Callable[[Principal], Awaitable[Principal]]:
    """Dependency factory that enforces presence of all `roles` on the principal."""
    required = frozenset(roles)

    async def _checker(principal: PrincipalDep) -> Principal:
        missing = required - principal.roles
        if missing:
            # Authenticated but lacking the role → 403 (not 401).
            raise PermissionDeniedError(f"Missing required roles: {sorted(missing)}")
        return principal

    return _checker


# ---------------------------------------------------------------------------
# Keycloak Admin
# ---------------------------------------------------------------------------


def _keycloak_admin_optional(request: Request) -> KeycloakAdminClient | None:
    return request.app.state.keycloak_admin  # type: ignore[no-any-return]


KeycloakAdminOptionalDep = Annotated[KeycloakAdminClient | None, Depends(_keycloak_admin_optional)]


def _keycloak_admin_required(kc: KeycloakAdminOptionalDep) -> KeycloakAdminClient:
    if kc is None:
        raise ServiceUnavailableError("Keycloak Admin integration is not configured")
    return kc


KeycloakAdminDep = Annotated[KeycloakAdminClient, Depends(_keycloak_admin_required)]


# ---------------------------------------------------------------------------
# Object storage (documents module)
# ---------------------------------------------------------------------------


def _object_store_optional(request: Request) -> ObjectStorePort | None:
    return request.app.state.object_store  # type: ignore[no-any-return]


ObjectStoreOptionalDep = Annotated["ObjectStorePort | None", Depends(_object_store_optional)]


def _object_store_required(store: ObjectStoreOptionalDep) -> ObjectStorePort:
    if store is None:
        # Generic core error (not documents' ObjectStoreUnavailableError) — importing
        # that here would create a core -> modules dependency this accessor avoids.
        raise ServiceUnavailableError("Object storage is not configured")
    return store


ObjectStoreDep = Annotated["ObjectStorePort", Depends(_object_store_required)]


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

# Non-safe HTTP methods — rate-limited as writes and CSRF-checked.
_WRITE_METHODS = frozenset({"POST", "PATCH", "PUT", "DELETE"})


def client_ip(request: Request) -> str:
    """Client IP for unauthenticated rate limiting.

    Prefers the edge proxy's X-Real-IP header. nginx sets it from $remote_addr with
    proxy_set_header (overwrite, not append), so a client-supplied value is replaced
    by the real connecting IP — unlike X-Forwarded-For, whose leftmost entry uvicorn
    trusts under `--forwarded-allow-ips *` and which nginx merely *appends* to, leaving
    it attacker-spoofable. X-Real-IP also stays per-client behind nginx (request.client
    would otherwise collapse to the proxy's container IP for every caller).

    Deployment requirement: the backend must only be reachable through the edge proxy
    that sets X-Real-IP; do not expose it directly. With no proxy (e.g. local direct
    hits) we fall back to the socket peer.
    """
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    client = request.client
    return client.host if client is not None else "unknown"


async def check_rate_limit(
    request: Request,
    principal: PrincipalDep,
    limiter: RateLimiterDep,
    settings: SettingsDep,
) -> None:
    """Enforce per-user (and aggregate per-tenant) rate limits. 429 on breach."""
    user_id = principal.subject

    if not await limiter.allow(
        f"global:{user_id}",
        limit=settings.rate_limit_global_per_minute,
        window_seconds=60,
    ):
        raise RateLimitError("Too many requests. Please slow down.")

    # Aggregate per-tenant ceiling — backstop against one tenant spreading load
    # across many distinct subjects to slip under the per-subject limit.
    if settings.rate_limit_tenant_per_minute > 0 and not await limiter.allow(
        f"tenant:{principal.tenant_id}",
        limit=settings.rate_limit_tenant_per_minute,
        window_seconds=60,
    ):
        raise RateLimitError("Tenant request quota exceeded. Please slow down.")

    if request.method in _WRITE_METHODS and not await limiter.allow(
        f"writes:{user_id}",
        limit=settings.rate_limit_writes_per_minute,
        window_seconds=60,
    ):
        raise RateLimitError("Too many write requests. Please slow down.")


RateLimitDep = Annotated[None, Depends(check_rate_limit)]


async def check_anon_rate_limit(
    request: Request,
    limiter: RateLimiterDep,
    settings: SettingsDep,
) -> None:
    """IP-keyed rate limit for unauthenticated / pre-auth endpoints (login, callback).

    Does NOT depend on a Principal, so it actually runs before authentication.
    Fail-closed: a flooded/unavailable Redis must not hand an attacker unlimited
    login attempts that fill the session store or hammer Keycloak.
    """
    ip = client_ip(request)
    if not await limiter.allow(
        f"auth:{ip}",
        limit=settings.rate_limit_auth_per_minute_per_ip,
        window_seconds=60,
        fail_closed=True,
    ):
        raise RateLimitError("Too many authentication attempts. Please try again shortly.")


AnonRateLimitDep = Annotated[None, Depends(check_anon_rate_limit)]


async def check_signup_rate_limit(
    request: Request,
    limiter: RateLimiterDep,
    settings: SettingsDep,
) -> None:
    """Strict per-IP cap for self-service signup, over a 1-hour window.

    Signup is unauthenticated and triggers expensive provisioning (a Keycloak
    group + user and a per-tenant schema migration), so it needs a tighter, longer
    ceiling than the per-minute auth burst limit it is layered on top of. Fail-closed:
    an unavailable Redis must not turn signup into an unbounded provisioning amplifier.
    """
    ip = client_ip(request)
    if not await limiter.allow(
        f"signup:{ip}",
        limit=settings.rate_limit_signup_per_hour_per_ip,
        window_seconds=3600,
        fail_closed=True,
    ):
        raise RateLimitError("Too many signup attempts. Please try again later.")

    # Aggregate backstop across all IPs — distributed sources each stay under the
    # per-IP cap but together could still flood the expensive provisioning saga.
    if settings.rate_limit_signup_global_per_hour > 0 and not await limiter.allow(
        "signup:global",
        limit=settings.rate_limit_signup_global_per_hour,
        window_seconds=3600,
        fail_closed=True,
    ):
        raise RateLimitError("Signups are temporarily unavailable. Please try again later.")


SignupRateLimitDep = Annotated[None, Depends(check_signup_rate_limit)]


async def check_csrf(request: Request, settings: SettingsDep) -> None:
    """Defense-in-depth CSRF check for cookie-authenticated, state-changing requests.

    SameSite=Lax + JSON content-type + the CORS allowlist already block classic
    form/fetch CSRF; this adds an explicit Origin/Referer allowlist so a future
    SameSite regression or a sibling-subdomain foothold cannot forge writes.

    Browsers always attach Origin to cross-site unsafe requests, so a present-but-
    mismatched Origin is rejected. A wholly absent Origin AND Referer is allowed
    (non-browser clients: server-to-server, curl, tests) — SameSite still covers
    the browser case.
    """
    if request.method not in _WRITE_METHODS:
        return
    allowed = settings.csrf_allowed_origins
    origin = request.headers.get("origin")
    if origin is not None:
        if origin.rstrip("/") not in allowed:
            raise CsrfError("Cross-origin request blocked")
        return
    referer = request.headers.get("referer")
    if referer is not None and not any(
        referer.rstrip("/") == o or referer.startswith(o + "/") for o in allowed
    ):
        raise CsrfError("Cross-origin request blocked")


CsrfDep = Annotated[None, Depends(check_csrf)]
