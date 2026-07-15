"""Authentication endpoints — Backend-for-Frontend OIDC bridge.

Flow:
  1. Browser → GET  /v1/auth/login?return_to=/tasks
     Backend generates state + PKCE, stores them in Redis, sets a short-lived
     `oidc_state` cookie on the initiating browser, and 307s to Keycloak.

  2. Browser → Keycloak (user logs in)
     Keycloak → 302 to /v1/auth/callback?code=...&state=...

  3. Backend requires the request to (a) carry the `oidc_state` cookie that
     was set at step 1 AND (b) have it match the `state` query param —
     constant-time compare. Without that binding, an attacker could relay a
     valid (code, state) pair to a victim and have THEIR browser land a
     session for the attacker's identity (OIDC Login CSRF / session fixation,
     RFC 6749 §10.12). After validation it pops the login_state from Redis,
     exchanges code → tokens, and creates the actual user session.

  4. All future API calls: browser sends the session cookie; the SessionDep
     in deps.py turns it into a Principal.

  5. POST /v1/auth/logout — invalidates server-side session, clears cookie,
     revokes refresh token at Keycloak.

  6. GET /v1/auth/me — returns minimal user info (subject + roles) without
     exposing the JWT to the SPA.
"""

from __future__ import annotations

import secrets
import time
from urllib.parse import quote

import structlog
from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse, RedirectResponse, Response

from app.config import Settings
from app.core.deps import (
    OIDCDep,
    PrincipalDep,
    SessionStoreDep,
    check_anon_rate_limit,
    check_csrf,
)
from app.core.errors import DomainError
from app.core.oidc import OIDCError, generate_pkce_pair, generate_state
from app.core.sessions import LoginState, SessionData

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

# Cookie that binds an OIDC `state` value to the browser that initiated /login.
# Scoped to /v1/auth/callback so it is never sent to any other endpoint.
_OIDC_STATE_COOKIE = "oidc_state"
_OIDC_STATE_COOKIE_TTL_SECONDS = 300  # match LoginState TTL in Redis


def _oidc_state_cookie_name(settings: Settings) -> str:
    # `__Secure-` (not `__Host-`) because the cookie is path-scoped to /callback.
    return f"__Secure-{_OIDC_STATE_COOKIE}" if settings.cookies_secure else _OIDC_STATE_COOKIE


class _AuthFlowError(DomainError):
    code = "AUTH_FLOW_ERROR"
    http_status = 400


def _callback_url(public_base_url: str) -> str:
    return public_base_url.rstrip("/") + "/v1/auth/callback"


def _callback_cookie_path() -> str:
    return "/v1/auth/callback"


def _is_safe_return_to(return_to: str | None) -> str:
    """Allow only same-origin paths to prevent open-redirect attacks."""
    if not return_to or not return_to.startswith("/") or return_to.startswith("//"):
        return "/"
    return return_to


# ---------------------------------------------------------------------------
# Login — kicks off the OIDC dance
# ---------------------------------------------------------------------------


@router.get(
    "/login",
    summary="Begin OIDC login flow",
    dependencies=[Depends(check_anon_rate_limit)],
)
async def login(
    request: Request,
    oidc: OIDCDep,
    sessions: SessionStoreDep,
    return_to: str = "/",
) -> RedirectResponse:
    settings: Settings = request.app.state.settings
    state = generate_state()
    pkce = generate_pkce_pair()
    redirect_uri = _callback_url(str(settings.public_base_url))

    await sessions.save_login_state(
        state,
        LoginState(
            pkce_verifier=pkce.verifier,
            redirect_uri=redirect_uri,
            return_to=_is_safe_return_to(return_to),
        ),
    )

    authorize_url = oidc.build_authorize_url(
        redirect_uri=redirect_uri,
        state=state,
        pkce_challenge=pkce.challenge,
    )
    response = RedirectResponse(authorize_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
    _set_oidc_state_cookie(response, state, settings)
    logger.info("auth.login_redirect", state_prefix=state[:6])
    return response


# ---------------------------------------------------------------------------
# Callback — Keycloak comes back here
# ---------------------------------------------------------------------------


@router.get(
    "/callback",
    summary="OIDC redirect target",
    dependencies=[Depends(check_anon_rate_limit)],
)
async def callback(
    request: Request,
    oidc: OIDCDep,
    sessions: SessionStoreDep,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> Response:
    settings: Settings = request.app.state.settings
    frontend = str(settings.frontend_base_url).rstrip("/")

    if error:
        logger.warning("auth.callback_idp_error", error=error)
        # URL-encode the IdP-supplied error before reflecting it into Location.
        resp = RedirectResponse(f"{frontend}/?auth_error={quote(error, safe='')}", status_code=302)
        _clear_oidc_state_cookie(resp, settings)
        return resp

    if not code or not state:
        raise _AuthFlowError("Missing 'code' or 'state' parameter")

    # Bind the response to the browser that initiated /login. The cookie was
    # set at /login with Path=/v1/auth/callback so only that browser carries
    # it here. Without this check, anyone relayed a (code, state) pair could
    # have a session minted in their browser for the original user.
    cookie_state = request.cookies.get(_oidc_state_cookie_name(settings))
    if cookie_state is None or not secrets.compare_digest(cookie_state, state):
        logger.warning(
            "auth.callback_state_cookie_mismatch",
            state_prefix=state[:6],
            cookie_present=cookie_state is not None,
        )
        # Burn the Redis entry so this (code, state) can't be retried by anyone.
        await sessions.pop_login_state(state)
        # Self-heal instead of dead-ending on a raw 400. The oidc_state cookie is
        # frequently absent/stale on the FIRST attempt — it has a 5-min TTL, and
        # an abandoned earlier flow (or cookie churn) can leave it missing or
        # mismatched. Bounce back to the SPA, which re-runs /login with a FRESH
        # state; Keycloak then silently re-authenticates from its SSO session and
        # the retry's cookie matches. The SPA's redirect loop-breaker (3 / 10s)
        # stops a genuinely broken cookie from looping forever.
        #
        # Security is preserved: NO session is minted here, so a relayed
        # (code, state) pair still cannot fixate a session — the failure only
        # restarts the legitimate flow in the browser that actually navigated here.
        resp_err = RedirectResponse(f"{frontend}/?auth_error=session", status_code=302)
        _clear_oidc_state_cookie(resp_err, settings)
        return resp_err

    login_state = await sessions.pop_login_state(state)
    if login_state is None:
        logger.warning("auth.callback_unknown_state", state_prefix=state[:6])
        raise _AuthFlowError("Unknown or expired login state")

    try:
        tokens = await oidc.exchange_code(
            code=code,
            redirect_uri=login_state.redirect_uri,
            pkce_verifier=login_state.pkce_verifier,
        )
    except OIDCError as exc:
        logger.warning("auth.code_exchange_failed", cause=exc.cause)
        raise _AuthFlowError("Login failed during token exchange") from exc

    # Verify the access_token locally so we don't trust whatever just came back
    # from Keycloak blindly — local import keeps the module-load cycle short.
    from app.core.security import verify_token

    jwks = request.app.state.jwks
    principal = await verify_token(
        tokens.access_token,
        jwks=jwks,
        audience=settings.keycloak_audience,
        tenant_claim=settings.keycloak_tenant_claim,
        roles_claim=settings.keycloak_roles_claim,
        leeway_seconds=settings.keycloak_leeway_seconds,
    )

    now = int(time.time())
    sid = await sessions.create(
        SessionData(
            subject=principal.subject,
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            id_token=tokens.id_token,
            access_expires_at=now + tokens.expires_in,
            created_at=now,
        )
    )

    response = RedirectResponse(f"{frontend}{login_state.return_to}", status_code=302)
    _set_session_cookie(response, sid, settings)
    _clear_oidc_state_cookie(response, settings)
    logger.info("auth.session_created", subject=principal.subject)
    return response


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


@router.post(
    "/logout",
    summary="Terminate the session",
    dependencies=[Depends(check_csrf)],
)
async def logout(request: Request, oidc: OIDCDep, sessions: SessionStoreDep) -> Response:
    settings: Settings = request.app.state.settings
    sid = request.cookies.get(settings.session_cookie_effective_name)

    logout_url: str | None = None
    if sid:
        sess = await sessions.get(sid)
        if sess is not None:
            if sess.refresh_token:
                # Backchannel revoke kills the refresh token at Keycloak.
                await oidc.revoke_refresh_token(refresh_token=sess.refresh_token)
            # RP-Initiated Logout: hand the SPA an end_session URL so it can also
            # clear the browser's Keycloak SSO session (otherwise a later /login
            # silently re-authenticates on shared devices). id_token_hint lets
            # Keycloak skip the confirmation prompt.
            logout_url = oidc.build_logout_url(
                post_logout_redirect_uri=str(settings.frontend_base_url).rstrip("/") + "/",
                id_token_hint=sess.id_token,
            )
        await sessions.delete(sid)

    response = JSONResponse({"status": "ok", "logout_url": logout_url})
    _clear_session_cookie(response, settings)
    return response


# ---------------------------------------------------------------------------
# Me — minimal user info for the SPA
# ---------------------------------------------------------------------------


@router.get("/me", summary="Current user info")
async def me(principal: PrincipalDep) -> dict[str, object]:
    return {
        "subject": principal.subject,
        "tenant_id": str(principal.tenant_id),
        "roles": sorted(principal.roles),
    }


# ---------------------------------------------------------------------------
# Cookie helpers
# ---------------------------------------------------------------------------


def _set_session_cookie(response: Response, sid: str, settings: Settings) -> None:
    response.set_cookie(
        key=settings.session_cookie_effective_name,
        value=sid,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        # Secure tracks the deployment scheme (https → Secure), so any TLS
        # deployment — not just prod — gets a Secure cookie. The `__Host-` prefix
        # (chosen with the name) additionally requires Secure + Path=/ + no Domain.
        secure=settings.cookies_secure,
        # 'lax' is required so the cookie survives the OIDC top-level redirect.
        samesite="lax",
        path="/",
    )


def _clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.session_cookie_effective_name,
        path="/",
        httponly=True,
        secure=settings.cookies_secure,
        samesite="lax",
    )


def _set_oidc_state_cookie(response: Response, state: str, settings: Settings) -> None:
    response.set_cookie(
        key=_oidc_state_cookie_name(settings),
        value=state,
        max_age=_OIDC_STATE_COOKIE_TTL_SECONDS,
        httponly=True,
        secure=settings.cookies_secure,
        # 'lax' lets the cookie survive the IdP redirect back to /callback,
        # which is a top-level GET navigation; 'strict' would block it.
        samesite="lax",
        path=_callback_cookie_path(),
    )


def _clear_oidc_state_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=_oidc_state_cookie_name(settings),
        path=_callback_cookie_path(),
        httponly=True,
        secure=settings.cookies_secure,
        samesite="lax",
    )
