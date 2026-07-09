"""RBAC + cross-tenant-invariant tests for the admin endpoints.

The provisioning service and the principal are dependency-overridden, so no real
Keycloak/DB is touched — these tests assert routing, role guards, and that the
member's tenant_id comes from the token, not the request body.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import httpx
import pytest

from app.core import deps
from app.core.security import Principal
from app.modules.tenants.application.services import MemberInvited, TenantOnboarded
from app.modules.tenants.interface import providers

_TID = UUID("11111111-1111-1111-1111-111111111111")
_OTHER = UUID("22222222-2222-2222-2222-222222222222")


def _principal(*roles: str) -> Principal:
    return Principal(
        subject="b79ac28a-7f95-4b15-8e22-52308f55eb99",
        tenant_id=_TID,
        roles=frozenset(roles),
        raw_claims={},
    )


class _FakeService:
    def __init__(self) -> None:
        self.onboarded: list[tuple[str, str, str]] = []
        self.invited: list[tuple[UUID, str, tuple[str, ...]]] = []

    async def onboard_tenant(self, *, slug: str, name: str, admin_email: str) -> TenantOnboarded:
        self.onboarded.append((slug, name, admin_email))
        return TenantOnboarded(
            tenant_id=_OTHER, slug=slug, keycloak_group_id="grp", admin_user_id="adm"
        )

    async def invite_member(self, *, tenant_id: UUID, email: str, roles: Any) -> MemberInvited:
        self.invited.append((tenant_id, email, tuple(roles)))
        return MemberInvited(user_id="usr", tenant_id=tenant_id, email=email)


async def _client(*roles: str) -> AsyncIterator[tuple[httpx.AsyncClient, _FakeService]]:
    # Deferred import: app.main builds the app at module load, which needs env vars
    # the conftest fixture sets at runtime (not at collection/import time).
    from app.main import create_app

    app = create_app()
    fake = _FakeService()
    app.dependency_overrides[deps._principal] = lambda: _principal(*roles)
    app.dependency_overrides[providers.provisioning_service] = lambda: fake
    # check_rate_limit needs app.state.rate_limiter (set by the lifespan we skip here);
    # these tests cover RBAC/routing, not throttling, so stub it out.
    app.dependency_overrides[deps.check_rate_limit] = lambda: None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as ac:
        yield ac, fake


@pytest.mark.asyncio
async def test_platform_admin_can_onboard_tenant() -> None:
    async for ac, fake in _client("platform_admin"):
        r = await ac.post(
            "/v1/admin/tenants",
            json={"slug": "acme", "name": "ACME Corp", "admin_email": "boss@acme.io"},
        )
        assert r.status_code == 201, r.text
        assert r.json()["slug"] == "acme"
        assert fake.onboarded == [("acme", "ACME Corp", "boss@acme.io")]


@pytest.mark.asyncio
async def test_non_platform_admin_cannot_onboard_tenant() -> None:
    async for ac, fake in _client("tenant_admin", "tenant_user"):
        r = await ac.post(
            "/v1/admin/tenants",
            json={"slug": "acme", "name": "ACME", "admin_email": "a@b.io"},
        )
        assert r.status_code == 403, r.text
        assert fake.onboarded == []


@pytest.mark.asyncio
async def test_tenant_admin_invites_into_own_tenant() -> None:
    async for ac, fake in _client("tenant_admin"):
        r = await ac.post("/v1/admin/users", json={"email": "emp@acme.io"})
        assert r.status_code == 201, r.text
        # tenant_id is the caller's, defaulted role is tenant_user
        assert fake.invited == [(_TID, "emp@acme.io", ("tenant_user",))]


@pytest.mark.asyncio
async def test_tenant_user_cannot_invite() -> None:
    async for ac, fake in _client("tenant_user"):
        r = await ac.post("/v1/admin/users", json={"email": "emp@acme.io"})
        assert r.status_code == 403, r.text
        assert fake.invited == []


@pytest.mark.asyncio
async def test_tenant_admin_cannot_grant_platform_admin() -> None:
    async for ac, fake in _client("tenant_admin"):
        r = await ac.post(
            "/v1/admin/users",
            json={"email": "emp@acme.io", "roles": ["platform_admin"]},
        )
        assert r.status_code == 422  # schema rejects the disallowed role
        assert fake.invited == []
