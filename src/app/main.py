from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app import __version__
from app.api.v1 import router as v1_router
from app.config import Settings, get_settings
from app.core.db import dispose_engine, get_engine
from app.core.error_handlers import register_exception_handlers
from app.core.events import wire_event_handlers
from app.core.keycloak_admin import KeycloakAdminClient
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.core.oidc import OIDCClient
from app.core.rate_limit import RateLimiter
from app.core.security import JWKSCache
from app.core.security_headers import LimitBodySizeMiddleware, SecurityHeadersMiddleware
from app.core.sessions import SessionStore

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings

    # Fail fast on bad production configuration before accepting traffic
    settings.validate_for_production()

    # Nudge (not a hard failure): public signup without email verification lets a
    # caller register an address they don't own (squatting). Safe for internal/no-SMTP
    # deployments; enable SIGNUP_REQUIRE_EMAIL_VERIFICATION for internet-facing ones.
    if (
        settings.is_prod
        and settings.signup_enabled
        and not settings.signup_require_email_verification
    ):
        logger.warning(
            "signup.email_verification_disabled",
            detail="SIGNUP_ENABLED=true without SIGNUP_REQUIRE_EMAIL_VERIFICATION; "
            "self-service founder emails are unverified. Enable it (needs SMTP) for public deployments.",
        )

    # Expected token `iss` is the public issuer; JWKS is fetched over the internal
    # (backchannel) URL — they differ when Keycloak is behind docker/ingress.
    internal_issuer = str(settings.keycloak_issuer).rstrip("/")
    app.state.jwks = JWKSCache(
        issuer=settings.keycloak_public_issuer_effective,
        jwks_uri=f"{internal_issuer}/protocol/openid-connect/certs",
        ttl_seconds=settings.keycloak_jwks_cache_ttl,
    )

    if settings.keycloak_admin_enabled:
        app.state.keycloak_admin = KeycloakAdminClient(
            issuer=str(settings.keycloak_issuer),
            realm=settings.keycloak_realm,
            client_id=settings.keycloak_admin_client_id,
            client_secret=settings.keycloak_admin_client_secret.get_secret_value(),
        )
        logger.info("keycloak_admin.enabled", realm=settings.keycloak_realm)
    else:
        app.state.keycloak_admin = None
        logger.info("keycloak_admin.disabled")

    # Rate-limit counters can live on a dedicated Redis so pressure on the session
    # store cannot disable the limiter (falls back to redis_url when unset).
    app.state.rate_limiter = RateLimiter(settings.rate_limit_redis_url_effective)

    app.state.oidc = OIDCClient(
        issuer=str(settings.keycloak_issuer),
        public_issuer=settings.keycloak_public_issuer_effective,
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret.get_secret_value(),
    )

    token_cipher = settings.build_token_cipher()
    if token_cipher is None:
        logger.warning("session_store.encryption_disabled")  # plaintext tokens at rest
    app.state.session_store = SessionStore(
        str(settings.redis_url),
        ttl_seconds=settings.session_ttl_seconds,
        idle_seconds=settings.session_idle_seconds,
        cipher=token_cipher,
    )

    get_engine(settings)
    # Wire domain-event subscribers (idempotent — safe on hot reload).
    wire_event_handlers()
    logger.info("app.started", env=settings.app_env, version=__version__)
    try:
        yield
    finally:
        await app.state.jwks.aclose()
        if app.state.keycloak_admin is not None:
            await app.state.keycloak_admin.aclose()
        await app.state.rate_limiter.aclose()
        await app.state.oidc.aclose()
        await app.state.session_store.aclose()
        await dispose_engine()
        logger.info("app.stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(
        title="SaaS Backend",
        version=__version__,
        debug=settings.app_debug,
        lifespan=lifespan,
        openapi_url="/openapi.json" if not settings.is_prod else None,
        docs_url="/docs" if not settings.is_prod else None,
        redoc_url=None,
    )
    app.state.settings = settings

    # Starlette wraps middleware so the LAST added is the OUTERMOST (it runs first
    # on the request and last on the response). They are therefore added inner→outer
    # below; the intended request-time order is the reverse of this call order:
    #   CORS → SecurityHeaders → RequestContext → TrustedHost → BodyLimit → routes.
    # Security headers / CORS sit outside the inner layers so even their error
    # responses (413, 400 bad-host, 500) carry the right headers.

    # Innermost: reject oversized payloads before the route consumes the body.
    app.add_middleware(LimitBodySizeMiddleware, max_bytes=settings.max_body_size_bytes)

    # Trusted host validation (only when an allowlist is configured)
    if settings.trusted_hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)

    # Request context (request-id, structured logging, duration)
    app.add_middleware(RequestContextMiddleware)

    # Security headers on every response (outside the layers above so their error
    # responses are decorated too).
    app.add_middleware(SecurityHeadersMiddleware, is_prod=settings.is_prod)

    # Outermost: CORS — allow_credentials=True is required so the browser sends
    # the session cookie cross-origin from the SPA in dev (5173 → 8000). Added
    # last so it wraps everything and error responses still receive CORS headers.
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[str(o).rstrip("/") for o in settings.cors_origins],
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["content-type", "x-request-id"],
        )

    register_exception_handlers(app)
    app.include_router(v1_router)

    return app


app = create_app()
