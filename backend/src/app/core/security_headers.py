"""Security headers + body-size limiting middleware.

SecurityHeadersMiddleware adds defensive HTTP headers to every response.
LimitBodySizeMiddleware rejects requests whose body exceeds a configured
threshold — both via the Content-Length header AND by counting the bytes that
actually stream in (so a chunked / Content-Length-spoofed request can't slip past).
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# API responses are JSON, never cacheable HTML/assets (nginx serves the SPA), so a
# strict policy is safe and acts as a backstop. Interactive docs (dev only) load
# CDN/inline assets and would break under it, so they're exempted.
_API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
_CSP_EXEMPT_PATHS = frozenset({"/docs", "/redoc", "/openapi.json"})


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds security headers to every response."""

    def __init__(self, app: ASGIApp, *, is_prod: bool = False) -> None:
        super().__init__(app)
        self._is_prod = is_prod

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        # Disable legacy XSS auditor (modern approach: rely on CSP / nosniff instead)
        response.headers["X-XSS-Protection"] = "0"
        # Authenticated/identity API responses must never be cached by browsers or
        # shared intermediaries (one user's /auth/me served to another).
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        if request.url.path not in _CSP_EXEMPT_PATHS:
            response.headers.setdefault("Content-Security-Policy", _API_CSP)
        if self._is_prod:
            # Only set HSTS when we know TLS is terminated upstream
            response.headers["Strict-Transport-Security"] = (
                "max-age=63072000; includeSubDomains; preload"
            )
        return response


class _BodyTooLarge(Exception):
    """Internal signal: streamed request body exceeded the configured cap."""


class LimitBodySizeMiddleware:
    """Rejects requests whose body exceeds max_bytes, before the route consumes it.

    Raw ASGI middleware (not BaseHTTPMiddleware) so it can intercept early and wrap
    `receive` to enforce the cap on the actual byte stream — catching chunked bodies
    and Content-Length spoofing that a header-only check would miss.
    """

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self._app = app
        self._max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                if int(content_length) > self._max_bytes:
                    await self._reject(scope, receive, send)
                    return
            except ValueError:
                pass  # malformed header — fall through to streamed counting

        received = 0
        response_started = False

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self._max_bytes:
                    raise _BodyTooLarge()
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self._app(scope, counting_receive, tracking_send)
        except _BodyTooLarge:
            if response_started:
                raise  # too late to send a clean 413
            await self._reject(scope, receive, send)

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            status_code=413,
            content={
                "error": {
                    "code": "PAYLOAD_TOO_LARGE",
                    "message": f"Request body exceeds the {self._max_bytes // 1024} KB limit",
                    "details": [],
                }
            },
        )
        await response(scope, receive, send)
