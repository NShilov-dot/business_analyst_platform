"""Endpoint tests for /api/v1/directory/users.

Covers:
- Tenant isolation: the Keycloak query is scoped by the verified principal's
  tenant_id (never request input).
- Happy path maps fields and excludes service accounts.
- Graceful degradation: kc unavailable (None) and Keycloak runtime errors both
  return 200 with an empty list rather than 500.
- Query-param forwarding and validation.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import httpx

from app.core import deps
from app.core.keycloak_admin import KeycloakAdminError
from app.core.security import Principal

_TID = UUID("11111111-1111-1111-1111-111111111111")

_RAW_USERS: list[dict[str, Any]] = [
    {
        "id": "aaaaaaaa-0000-0000-0000-000000000001",
        "username": "timur.rashidov",
        "firstName": "Timur",
        "lastName": "Rashidov",
        "email": "timur@demo.beeline.uz",
    },
    {
        # service account — must be excluded
        "id": "aaaaaaaa-0000-0000-0000-000000000002",
        "username": "service-account-bap-backend-admin",
    },
]


def _principal(*roles: str) -> Principal:
    return Principal(
        subject="b3885935-8399-40f3-af04-5e9aec6493ea",
        tenant_id=_TID,
        roles=frozenset(roles or ("tenant_user",)),
        raw_claims={},
    )


class _FakeKc:
    """Minimal stand-in for KeycloakAdminClient exposing only search_users."""

    def __init__(
        self, users: list[dict[str, Any]] | None = None, raises: Exception | None = None
    ) -> None:
        self._users = _RAW_USERS if users is None else users
        self._raises = raises
        self.calls: list[dict[str, Any]] = []

    async def search_users(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(kwargs)
        if self._raises:
            raise self._raises
        return self._users


async def _make_client(
    *,
    kc: _FakeKc | None,
    roles: tuple[str, ...] = ("tenant_user",),
) -> AsyncIterator[tuple[httpx.AsyncClient, _FakeKc | None]]:
    from app.main import create_app

    app = create_app()
    app.dependency_overrides[deps._principal] = lambda: _principal(*roles)
    app.dependency_overrides[deps._keycloak_admin_optional] = lambda: kc
    app.dependency_overrides[deps.check_rate_limit] = lambda: None
    app.dependency_overrides[deps.check_csrf] = lambda: None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as ac:
        yield ac, kc


async def test_directory_happy_path_maps_and_excludes_service_accounts() -> None:
    kc = _FakeKc()
    async for ac, _ in _make_client(kc=kc):
        r = await ac.get("/api/v1/directory/users")
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert len(data) == 1  # service account filtered out
        u = data[0]
        assert u["subject"] == "aaaaaaaa-0000-0000-0000-000000000001"
        assert u["username"] == "timur.rashidov"
        assert u["full_name"] == "Timur Rashidov"
        assert u["email"] == "timur@demo.beeline.uz"


async def test_directory_scopes_query_to_caller_tenant() -> None:
    kc = _FakeKc()
    async for ac, fake in _make_client(kc=kc):
        await ac.get("/api/v1/directory/users", params={"q": "tim", "limit": 25})
        assert fake is not None
        assert fake.calls, "search_users was not called"
        call = fake.calls[0]
        assert call["q"] == f"tenant_id:{_TID}"
        assert call["search"] == "tim"
        assert call["max_results"] == 25
        assert call["enabled"] is True  # disabled users excluded


async def test_directory_empty_when_kc_unavailable() -> None:
    async for ac, _ in _make_client(kc=None):
        r = await ac.get("/api/v1/directory/users")
        assert r.status_code == 200
        assert r.json()["data"] == []


async def test_directory_empty_on_keycloak_runtime_error() -> None:
    kc = _FakeKc(raises=KeycloakAdminError(503, "keycloak down"))
    async for ac, _ in _make_client(kc=kc):
        r = await ac.get("/api/v1/directory/users")
        assert r.status_code == 200, r.text
        assert r.json()["data"] == []


async def test_directory_empty_on_network_error() -> None:
    kc = _FakeKc(raises=httpx.ConnectError("boom"))
    async for ac, _ in _make_client(kc=kc):
        r = await ac.get("/api/v1/directory/users")
        assert r.status_code == 200, r.text
        assert r.json()["data"] == []


async def test_directory_limit_too_large_rejected() -> None:
    kc = _FakeKc()
    async for ac, _ in _make_client(kc=kc):
        r = await ac.get("/api/v1/directory/users", params={"limit": 500})
        assert r.status_code == 422
