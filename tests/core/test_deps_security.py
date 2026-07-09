"""Tests for CSRF origin enforcement."""

from __future__ import annotations

import pytest
from starlette.requests import Request

from app.config import Settings
from app.core.deps import check_csrf, client_ip
from app.core.errors import CsrfError

_SETTINGS = Settings(
    database_url="postgresql+asyncpg://app:app@db:5432/app",
    keycloak_audience="saas-backend",
    keycloak_issuer="https://kc/realms/saas",
    redis_url="redis://r:6379/0",
    frontend_base_url="https://fe.x",
    public_base_url="https://api.x",
)


def _request(method: str, headers: dict[str, str]) -> Request:
    scope = {
        "type": "http",
        "method": method,
        "path": "/v1/tasks",
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": ("1.2.3.4", 1234),
    }
    return Request(scope)


@pytest.mark.asyncio
async def test_csrf_allows_safe_method_regardless_of_origin() -> None:
    await check_csrf(_request("GET", {"origin": "https://evil.x"}), _SETTINGS)


@pytest.mark.asyncio
async def test_csrf_allows_matching_origin() -> None:
    await check_csrf(_request("POST", {"origin": "https://fe.x"}), _SETTINGS)


@pytest.mark.asyncio
async def test_csrf_blocks_foreign_origin() -> None:
    with pytest.raises(CsrfError):
        await check_csrf(_request("POST", {"origin": "https://evil.x"}), _SETTINGS)


@pytest.mark.asyncio
async def test_csrf_blocks_foreign_referer_when_no_origin() -> None:
    with pytest.raises(CsrfError):
        await check_csrf(_request("DELETE", {"referer": "https://evil.x/page"}), _SETTINGS)


@pytest.mark.asyncio
async def test_csrf_allows_when_no_origin_or_referer() -> None:
    # Non-browser clients (server-to-server, tests) — SameSite still guards browsers.
    await check_csrf(_request("POST", {}), _SETTINGS)


def test_client_ip_prefers_x_real_ip_over_socket() -> None:
    # X-Real-IP (set by the edge proxy from the real socket) wins over request.client,
    # which under --forwarded-allow-ips '*' would be the spoofable/collapsed value.
    req = _request("GET", {"x-real-ip": "203.0.113.7", "x-forwarded-for": "9.9.9.9"})
    assert client_ip(req) == "203.0.113.7"


def test_client_ip_falls_back_to_socket_when_no_header() -> None:
    assert client_ip(_request("GET", {})) == "1.2.3.4"
