"""Unit tests for KeycloakAdminClient.

No real Keycloak required — all HTTP is intercepted via a custom
httpx.AsyncBaseTransport that matches (method, url-substring) pairs.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx
import pytest

from app.core.keycloak_admin import KeycloakAdminClient, KeycloakAdminError, TenantGroupSpec

_ISSUER = "http://keycloak:8080/realms/bap"
_TOKEN_BODY = {"access_token": "tok-abc", "expires_in": 300}
_TOKEN_URL_PART = "openid-connect/token"
_ADMIN_BASE_PART = "/admin/realms/bap"


# ---------------------------------------------------------------------------
# Test transport
# ---------------------------------------------------------------------------


@dataclass
class _Route:
    method: str
    url_part: str
    status: int
    json: dict | list | None = None
    text: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    calls: int = field(default=0, init=False)


class _FakeTransport(httpx.AsyncBaseTransport):
    def __init__(self, *routes: _Route) -> None:
        self._routes = list(routes)
        self.all_calls: list[str] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.all_calls.append(f"{request.method} {url}")
        for route in self._routes:
            if request.method == route.method and route.url_part in url:
                route.calls += 1
                if route.json is not None:
                    return httpx.Response(route.status, json=route.json, headers=route.headers)
                return httpx.Response(route.status, text=route.text, headers=route.headers)
        raise AssertionError(f"No route matched: {request.method} {url!r}")


def _token_route() -> _Route:
    return _Route("POST", _TOKEN_URL_PART, 200, json=_TOKEN_BODY)


def _client(*extra_routes: _Route) -> tuple[KeycloakAdminClient, _FakeTransport]:
    transport = _FakeTransport(_token_route(), *extra_routes)
    http = httpx.AsyncClient(transport=transport)
    kc = KeycloakAdminClient(
        issuer=_ISSUER,
        realm="bap",
        client_id="admin-svc",
        client_secret="secret",
        _http=http,
    )
    return kc, transport


# ---------------------------------------------------------------------------
# Token management
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_acquired_on_first_call() -> None:
    kc, transport = _client(
        _Route("GET", f"{_ADMIN_BASE_PART}/users/u1", 200, json={"id": "u1"}),
    )
    await kc.get_user("u1")
    token_route = transport._routes[0]
    assert token_route.calls == 1


@pytest.mark.asyncio
async def test_token_reused_within_ttl() -> None:
    kc, transport = _client(
        _Route("GET", f"{_ADMIN_BASE_PART}/users/u1", 200, json={"id": "u1"}),
    )
    await kc.get_user("u1")
    await kc.get_user("u1")
    token_route = transport._routes[0]
    assert token_route.calls == 1


@pytest.mark.asyncio
async def test_token_refreshed_after_expiry() -> None:
    kc, transport = _client(
        _Route("GET", f"{_ADMIN_BASE_PART}/users/u1", 200, json={"id": "u1"}),
    )
    await kc.get_user("u1")
    # Force-expire the cached token
    assert kc._token is not None
    kc._token.expires_at = time.monotonic() - 1

    # Second GET must re-acquire — but the transport only has one token route,
    # so we need to add another; reset call count instead by re-checking route 0.
    await kc.get_user("u1")
    token_route = transport._routes[0]
    assert token_route.calls == 2


@pytest.mark.asyncio
async def test_token_error_raises_keycloak_admin_error() -> None:
    transport = _FakeTransport(
        _Route("POST", _TOKEN_URL_PART, 401, text="Unauthorized"),
    )
    http = httpx.AsyncClient(transport=transport)
    kc = KeycloakAdminClient(
        issuer=_ISSUER,
        realm="bap",
        client_id="bad",
        client_secret="bad",
        _http=http,
    )
    with pytest.raises(KeycloakAdminError) as exc_info:
        await kc.get_user("x")
    assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# Groups
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_group_returns_id_from_location() -> None:
    group_id = "kc-group-abc"
    kc, _ = _client(
        _Route(
            "POST",
            f"{_ADMIN_BASE_PART}/groups",
            201,
            json={},
            headers={"Location": f"http://keycloak:8080/admin/realms/bap/groups/{group_id}"},
        ),
    )
    result = await kc.create_group("tenant_acme", attributes={"tenant_id": ["uuid-1"]})
    assert result == group_id


@pytest.mark.asyncio
async def test_get_group() -> None:
    payload = {"id": "g1", "name": "tenant_acme"}
    kc, _ = _client(
        _Route("GET", f"{_ADMIN_BASE_PART}/groups/g1", 200, json=payload),
    )
    result = await kc.get_group("g1")
    assert result == payload


@pytest.mark.asyncio
async def test_delete_group() -> None:
    route = _Route("DELETE", f"{_ADMIN_BASE_PART}/groups/g1", 204)
    kc, _ = _client(route)
    await kc.delete_group("g1")
    assert route.calls == 1


@pytest.mark.asyncio
async def test_list_group_members() -> None:
    members = [{"id": "u1"}, {"id": "u2"}]
    kc, _ = _client(
        _Route("GET", f"{_ADMIN_BASE_PART}/groups/g1/members", 200, json=members),
    )
    result = await kc.list_group_members("g1")
    assert result == members


@pytest.mark.asyncio
async def test_add_user_to_group() -> None:
    route = _Route("PUT", f"{_ADMIN_BASE_PART}/users/u1/groups/g1", 204)
    kc, _ = _client(route)
    await kc.add_user_to_group("u1", "g1")
    assert route.calls == 1


@pytest.mark.asyncio
async def test_remove_user_from_group() -> None:
    route = _Route("DELETE", f"{_ADMIN_BASE_PART}/users/u1/groups/g1", 204)
    kc, _ = _client(route)
    await kc.remove_user_from_group("u1", "g1")
    assert route.calls == 1


@pytest.mark.asyncio
async def test_unexpected_status_raises_error() -> None:
    kc, _ = _client(
        _Route("GET", f"{_ADMIN_BASE_PART}/groups/missing", 404, text="Not Found"),
    )
    with pytest.raises(KeycloakAdminError) as exc_info:
        await kc.get_group("missing")
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_user() -> None:
    payload = {"id": "u1", "username": "alice"}
    kc, _ = _client(
        _Route("GET", f"{_ADMIN_BASE_PART}/users/u1", 200, json=payload),
    )
    result = await kc.get_user("u1")
    assert result == payload


@pytest.mark.asyncio
async def test_find_users_passes_params() -> None:
    transport = _FakeTransport(
        _token_route(),
        _Route("GET", f"{_ADMIN_BASE_PART}/users", 200, json=[{"id": "u1"}]),
    )
    http = httpx.AsyncClient(transport=transport)
    kc = KeycloakAdminClient(
        issuer=_ISSUER, realm="bap", client_id="c", client_secret="s", _http=http
    )
    result = await kc.find_users(email="alice@example.com")
    assert result == [{"id": "u1"}]
    # Verify the email param was sent
    get_call = next(c for c in transport.all_calls if "/users" in c and "POST" not in c)
    assert "alice%40example.com" in get_call or "email" in get_call


@pytest.mark.asyncio
async def test_create_user_returns_id_from_location() -> None:
    uid = "user-xyz"
    route = _Route(
        "POST",
        f"{_ADMIN_BASE_PART}/users",
        201,
        json={},
        headers={"Location": f"http://keycloak:8080/admin/realms/bap/users/{uid}"},
    )
    kc, _ = _client(route)
    result = await kc.create_user(
        username="bob@example.com",
        email="bob@example.com",
        attributes={"tenant_id": ["t-1"]},
    )
    assert result == uid
    assert route.calls == 1


@pytest.mark.asyncio
async def test_assign_realm_role_looks_up_then_posts() -> None:
    get_role = _Route(
        "GET",
        f"{_ADMIN_BASE_PART}/roles/tenant_user",
        200,
        json={"id": "r1", "name": "tenant_user"},
    )
    post_map = _Route("POST", f"{_ADMIN_BASE_PART}/users/u1/role-mappings/realm", 204)
    kc, _ = _client(get_role, post_map)
    await kc.assign_realm_role("u1", "tenant_user")
    assert get_role.calls == 1 and post_map.calls == 1


@pytest.mark.asyncio
async def test_send_execute_actions_email() -> None:
    route = _Route("PUT", f"{_ADMIN_BASE_PART}/users/u1/execute-actions-email", 204)
    kc, transport = _client(route)
    await kc.send_execute_actions_email(
        "u1", ["UPDATE_PASSWORD", "VERIFY_EMAIL"], client_id="bap-backend"
    )
    assert route.calls == 1
    put_call = next(c for c in transport.all_calls if "execute-actions-email" in c)
    assert "client_id=bap-backend" in put_call


@pytest.mark.asyncio
async def test_delete_user() -> None:
    route = _Route("DELETE", f"{_ADMIN_BASE_PART}/users/u1", 204)
    kc, _ = _client(route)
    await kc.delete_user("u1")
    assert route.calls == 1


@pytest.mark.asyncio
async def test_set_user_password() -> None:
    route = _Route("PUT", f"{_ADMIN_BASE_PART}/users/u1/reset-password", 204)
    kc, _ = _client(route)
    await kc.set_user_password("u1", "correct horse battery staple")
    assert route.calls == 1


@pytest.mark.asyncio
async def test_set_user_password_policy_rejection_raises_400() -> None:
    # Keycloak returns 400 when the realm password policy rejects the value.
    route = _Route(
        "PUT", f"{_ADMIN_BASE_PART}/users/u1/reset-password", 400, text="invalidPasswordMinLength"
    )
    kc, _ = _client(route)
    with pytest.raises(KeycloakAdminError) as exc_info:
        await kc.set_user_password("u1", "short")
    assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# TenantGroupSpec helper
# ---------------------------------------------------------------------------


def test_tenant_group_spec_group_name() -> None:
    spec = TenantGroupSpec(tenant_slug="acme", tenant_id="uuid-1")
    assert spec.group_name == "tenant_acme"


def test_tenant_group_spec_attributes() -> None:
    spec = TenantGroupSpec(tenant_slug="acme", tenant_id="uuid-1")
    assert spec.attributes == {"tenant_id": ["uuid-1"]}
