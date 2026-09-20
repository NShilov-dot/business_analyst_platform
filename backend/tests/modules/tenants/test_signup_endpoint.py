"""Endpoint tests for the public self-service signup (POST /api/v1/signup).

The provisioning service and the rate-limit dependencies are overridden, so no
real Keycloak/DB/Redis is touched — these assert routing, validation, the feature
flag, error mapping, and that the response leaks no internal Keycloak IDs.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

import httpx
import pytest

from app.core import deps
from app.core.errors import ConflictError, WeakPasswordError
from app.modules.tenants.application.services import TenantOnboarded
from app.modules.tenants.interface import providers

_TID = UUID("33333333-3333-3333-3333-333333333333")

_VALID_BODY = {
    "company_name": "ACME Corp",
    "slug": "acme",
    "admin_email": "founder@acme.io",
    "admin_password": "correct horse battery staple",
}


class _FakeService:
    def __init__(self, *, raises: Exception | None = None) -> None:
        self.calls: list[dict[str, str]] = []
        self._raises = raises

    async def register_tenant(
        self,
        *,
        slug: str,
        name: str,
        admin_email: str,
        admin_password: str,
        require_email_verification: bool = False,
    ) -> TenantOnboarded:
        self.calls.append(
            {
                "slug": slug,
                "name": name,
                "email": admin_email,
                "password": admin_password,
                "verify": require_email_verification,
            }
        )
        if self._raises is not None:
            raise self._raises
        return TenantOnboarded(
            tenant_id=_TID, slug=slug, keycloak_group_id="grp-secret", admin_user_id="usr-secret"
        )


async def _client(
    *, service: _FakeService | None = None, signup_enabled: bool = True
) -> AsyncIterator[tuple[httpx.AsyncClient, _FakeService]]:
    from app.config import get_settings
    from app.main import create_app

    app = create_app()
    fake = service or _FakeService()
    app.dependency_overrides[providers.provisioning_service] = lambda: fake
    # Rate limiters need app.state.rate_limiter (set by the lifespan we skip here);
    # these tests cover routing/validation, not throttling, so stub them out.
    app.dependency_overrides[deps.check_anon_rate_limit] = lambda: None
    app.dependency_overrides[deps.check_signup_rate_limit] = lambda: None
    if not signup_enabled:
        settings = get_settings().model_copy(update={"signup_enabled": False})
        app.dependency_overrides[deps._settings_dep] = lambda: settings
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as ac:
        yield ac, fake


@pytest.mark.asyncio
async def test_signup_creates_tenant() -> None:
    async for ac, fake in _client():
        r = await ac.post("/api/v1/signup", json=_VALID_BODY)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["slug"] == "acme"
        assert body["tenant_id"] == str(_TID)
        assert body["login_url"] == "/api/v1/auth/login?return_to=/"
        # The company name maps to the tenant name; password is forwarded verbatim.
        assert fake.calls == [
            {
                "slug": "acme",
                "name": "ACME Corp",
                "email": "founder@acme.io",
                "password": "correct horse battery staple",
                "verify": False,  # SIGNUP_REQUIRE_EMAIL_VERIFICATION defaults off
            }
        ]


@pytest.mark.asyncio
async def test_signup_response_leaks_no_internal_ids() -> None:
    async for ac, _ in _client():
        r = await ac.post("/api/v1/signup", json=_VALID_BODY)
        body = r.json()
        # Internal Keycloak identifiers must never reach an anonymous caller.
        assert "keycloak_group_id" not in body
        assert "admin_user_id" not in body
        assert "grp-secret" not in r.text and "usr-secret" not in r.text


@pytest.mark.asyncio
async def test_signup_disabled_returns_404() -> None:
    async for ac, fake in _client(signup_enabled=False):
        r = await ac.post("/api/v1/signup", json=_VALID_BODY)
        assert r.status_code == 404, r.text
        assert fake.calls == []  # provisioning never invoked


@pytest.mark.asyncio
async def test_signup_short_password_is_422() -> None:
    async for ac, fake in _client():
        r = await ac.post("/api/v1/signup", json={**_VALID_BODY, "admin_password": "short"})
        assert r.status_code == 422
        assert fake.calls == []


@pytest.mark.asyncio
async def test_signup_bad_slug_is_422() -> None:
    async for ac, fake in _client():
        r = await ac.post("/api/v1/signup", json={**_VALID_BODY, "slug": "Has Spaces!"})
        assert r.status_code == 422
        assert fake.calls == []


@pytest.mark.asyncio
async def test_signup_duplicate_slug_is_409() -> None:
    svc = _FakeService(raises=ConflictError("Tenant slug 'acme' already exists"))
    async for ac, _ in _client(service=svc):
        r = await ac.post("/api/v1/signup", json=_VALID_BODY)
        assert r.status_code == 409, r.text


@pytest.mark.asyncio
async def test_signup_weak_password_is_400() -> None:
    svc = _FakeService(raises=WeakPasswordError("Password does not meet the required policy"))
    async for ac, _ in _client(service=svc):
        r = await ac.post("/api/v1/signup", json=_VALID_BODY)
        assert r.status_code == 400, r.text
        assert r.json()["error"]["code"] == "WEAK_PASSWORD"
