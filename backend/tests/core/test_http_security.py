"""Tests for SecurityHeadersMiddleware (CSP / no-store) and the body-size cap.

Exercised against a minimal Starlette app so no Redis/DB/Keycloak is needed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

from app.core.security_headers import LimitBodySizeMiddleware, SecurityHeadersMiddleware

_MAX = 100  # tiny cap for the test


def _build_app() -> Starlette:
    async def ok(request: httpx.Request) -> PlainTextResponse:  # type: ignore[override]
        return PlainTextResponse("ok")

    async def echo(request) -> JSONResponse:  # type: ignore[no-untyped-def]
        body = await request.body()
        return JSONResponse({"len": len(body)})

    app = Starlette(
        routes=[
            Route("/thing", ok, methods=["GET"]),
            Route("/openapi.json", ok, methods=["GET"]),
            Route("/echo", echo, methods=["POST"]),
        ]
    )
    app.add_middleware(LimitBodySizeMiddleware, max_bytes=_MAX)
    app.add_middleware(SecurityHeadersMiddleware, is_prod=False)
    return app


async def _client() -> AsyncIterator[httpx.AsyncClient]:
    app = _build_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as ac:
        yield ac


@pytest.mark.asyncio
async def test_security_headers_present() -> None:
    async for ac in _client():
        r = await ac.get("/thing")
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert r.headers["X-Frame-Options"] == "DENY"
        assert r.headers["Cache-Control"] == "no-store"
        assert "default-src 'none'" in r.headers["Content-Security-Policy"]


@pytest.mark.asyncio
async def test_csp_exempt_for_docs_paths() -> None:
    async for ac in _client():
        r = await ac.get("/openapi.json")
        assert "Content-Security-Policy" not in r.headers


@pytest.mark.asyncio
async def test_body_within_limit_ok() -> None:
    async for ac in _client():
        r = await ac.post("/echo", content=b"x" * 50)
        assert r.status_code == 200
        assert r.json()["len"] == 50


@pytest.mark.asyncio
async def test_oversized_content_length_rejected() -> None:
    async for ac in _client():
        r = await ac.post("/echo", content=b"x" * (_MAX + 1))
        assert r.status_code == 413
        assert r.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


@pytest.mark.asyncio
async def test_oversized_chunked_body_rejected() -> None:
    """No Content-Length (streamed/chunked) must still be capped by byte counting."""

    async def gen() -> AsyncIterator[bytes]:
        for _ in range(10):
            yield b"x" * 20  # 200 bytes total > cap, sent without Content-Length

    async for ac in _client():
        r = await ac.post("/echo", content=gen())
        assert r.status_code == 413
