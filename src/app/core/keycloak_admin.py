"""Async Keycloak Admin REST API client.

Authenticates via client_credentials against the realm's own token endpoint.
The service-account client needs these realm-management roles (NOT the full
realm-admin composite): `manage-users` (users, role-mappings, group membership,
execute-actions-email), `manage-realm` (group CRUD), `view-realm` (read realm
roles for assign_realm_role). See realm-export.json.

URL layout (derived from issuer):
  token endpoint : {issuer}/protocol/openid-connect/token
  admin base     : {kc_base}/admin/realms/{realm}
                   where kc_base = issuer.rsplit("/realms/", 1)[0]
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

_TOKEN_REFRESH_BUFFER = 30  # seconds before expiry to pre-fetch


@dataclass(slots=True)
class _TokenState:
    access_token: str
    expires_at: float  # monotonic


class KeycloakAdminError(Exception):
    """Raised when the Keycloak Admin API returns an unexpected HTTP status."""

    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"Keycloak Admin API {status_code}: {body[:200]}")
        self.status_code = status_code
        self.body = body


class KeycloakAdminClient:
    """Async Keycloak Admin REST API client with automatic token caching.

    Supports async context manager usage::

        async with KeycloakAdminClient(...) as kc:
            gid = await kc.create_group("tenant_acme", attributes={"tenant_id": ["<uuid>"]})
    """

    def __init__(
        self,
        *,
        issuer: str,
        realm: str,
        client_id: str,
        client_secret: str,
        http_timeout: float = 10.0,
        _http: httpx.AsyncClient | None = None,
    ) -> None:
        issuer = issuer.rstrip("/")
        kc_base = issuer.rsplit("/realms/", 1)[0]
        self._realm = realm
        self._client_id = client_id
        self._client_secret = client_secret
        self._token_url = f"{issuer}/protocol/openid-connect/token"
        self._admin_base = f"{kc_base}/admin/realms/{realm}"
        self._token: _TokenState | None = None
        self._lock = asyncio.Lock()
        self._http = _http or httpx.AsyncClient(timeout=http_timeout)

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    async def _fetch_token(self) -> str:
        resp = await self._http.post(
            self._token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
        )
        if resp.status_code != 200:
            raise KeycloakAdminError(resp.status_code, resp.text)
        body = resp.json()
        self._token = _TokenState(
            access_token=body["access_token"],
            expires_at=time.monotonic() + body["expires_in"] - _TOKEN_REFRESH_BUFFER,
        )
        logger.debug("keycloak_admin.token_acquired", realm=self._realm)
        return self._token.access_token

    async def _get_token(self) -> str:
        if self._token is not None and time.monotonic() < self._token.expires_at:
            return self._token.access_token
        async with self._lock:
            if self._token is not None and time.monotonic() < self._token.expires_at:
                return self._token.access_token
            return await self._fetch_token()

    # ------------------------------------------------------------------
    # HTTP helper
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, str] | None = None,
        expect: set[int] | None = None,
    ) -> httpx.Response:
        token = await self._get_token()
        resp = await self._http.request(
            method,
            f"{self._admin_base}{path}",
            headers={"Authorization": f"Bearer {token}"},
            json=json,
            params=params,
        )
        allowed = expect if expect is not None else {200, 201, 204}
        if resp.status_code not in allowed:
            raise KeycloakAdminError(resp.status_code, resp.text)
        return resp

    # ------------------------------------------------------------------
    # Groups
    # ------------------------------------------------------------------

    async def create_group(
        self,
        name: str,
        *,
        attributes: dict[str, list[str]] | None = None,
    ) -> str:
        """Create a top-level realm group and return its Keycloak UUID."""
        payload: dict[str, Any] = {"name": name}
        if attributes:
            payload["attributes"] = attributes
        resp = await self._request("POST", "/groups", json=payload, expect={201})
        location: str = resp.headers.get("Location", "")
        group_id = location.rsplit("/", 1)[-1]
        logger.info(
            "keycloak_admin.group_created",
            realm=self._realm,
            name=name,
            group_id=group_id,
        )
        return group_id

    async def get_group(self, group_id: str) -> dict[str, Any]:
        """Fetch group details by ID."""
        resp = await self._request("GET", f"/groups/{group_id}")
        return resp.json()  # type: ignore[no-any-return]

    async def delete_group(self, group_id: str) -> None:
        """Delete a group by ID."""
        await self._request("DELETE", f"/groups/{group_id}", expect={204})
        logger.info("keycloak_admin.group_deleted", realm=self._realm, group_id=group_id)

    async def list_group_members(
        self,
        group_id: str,
        *,
        first: int = 0,
        max_results: int = 100,
    ) -> list[dict[str, Any]]:
        """Return up to `max_results` members of a group."""
        resp = await self._request(
            "GET",
            f"/groups/{group_id}/members",
            params={"first": str(first), "max": str(max_results)},
        )
        return resp.json()  # type: ignore[no-any-return]

    async def add_user_to_group(self, user_id: str, group_id: str) -> None:
        """Add user `user_id` to group `group_id`."""
        await self._request("PUT", f"/users/{user_id}/groups/{group_id}", expect={204})

    async def remove_user_from_group(self, user_id: str, group_id: str) -> None:
        """Remove user `user_id` from group `group_id`."""
        await self._request("DELETE", f"/users/{user_id}/groups/{group_id}", expect={204})

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------

    async def get_user(self, user_id: str) -> dict[str, Any]:
        """Fetch a user by Keycloak UUID."""
        resp = await self._request("GET", f"/users/{user_id}")
        return resp.json()  # type: ignore[no-any-return]

    async def find_users(
        self,
        *,
        username: str | None = None,
        email: str | None = None,
        max_results: int = 20,
    ) -> list[dict[str, Any]]:
        """Search users by username or email prefix."""
        params: dict[str, str] = {"max": str(max_results)}
        if username is not None:
            params["username"] = username
        if email is not None:
            params["email"] = email
        resp = await self._request("GET", "/users", params=params)
        return resp.json()  # type: ignore[no-any-return]

    async def search_users(
        self,
        *,
        q: str | None = None,
        search: str | None = None,
        enabled: bool | None = None,
        first: int = 0,
        max_results: int = 100,
    ) -> list[dict[str, Any]]:
        """Search users by attribute filter and/or name/username/email prefix.

        Args:
            q: Attribute search string (e.g. ``"tenant_id:<uuid>"``).  Passed
               as the Keycloak ``q`` query parameter which filters by user
               attributes.
            search: Prefix search across firstName, lastName, username and
               email — maps to Keycloak's ``search`` query parameter.
            enabled: When set, restrict to enabled (``True``) or disabled
               (``False``) users — maps to Keycloak's ``enabled`` parameter.
            first: Index of the first result (pagination offset).
            max_results: Maximum number of users to return.
        """
        params: dict[str, str] = {"first": str(first), "max": str(max_results)}
        if q is not None:
            params["q"] = q
        if search is not None:
            params["search"] = search
        if enabled is not None:
            params["enabled"] = "true" if enabled else "false"
        resp = await self._request("GET", "/users", params=params)
        return resp.json()  # type: ignore[no-any-return]

    async def create_user(
        self,
        *,
        username: str,
        email: str,
        first_name: str | None = None,
        last_name: str | None = None,
        attributes: dict[str, list[str]] | None = None,
        required_actions: list[str] | None = None,
        enabled: bool = True,
        email_verified: bool = False,
    ) -> str:
        """Create a realm user and return its Keycloak UUID.

        Note: custom attributes (e.g. tenant_id) are only persisted if the realm's
        user profile declares them or allows unmanaged attributes — see
        realm-export.json (unmanagedAttributePolicy).
        """
        payload: dict[str, Any] = {
            "username": username,
            "email": email,
            "enabled": enabled,
            "emailVerified": email_verified,
        }
        if first_name is not None:
            payload["firstName"] = first_name
        if last_name is not None:
            payload["lastName"] = last_name
        if attributes:
            payload["attributes"] = attributes
        if required_actions:
            payload["requiredActions"] = required_actions
        resp = await self._request("POST", "/users", json=payload, expect={201})
        location: str = resp.headers.get("Location", "")
        user_id = location.rsplit("/", 1)[-1]
        logger.info("keycloak_admin.user_created", realm=self._realm, user_id=user_id)
        return user_id

    async def delete_user(self, user_id: str) -> None:
        """Delete a user by ID (used to compensate a failed provisioning step)."""
        await self._request("DELETE", f"/users/{user_id}", expect={204})

    async def set_user_password(
        self, user_id: str, password: str, *, temporary: bool = False
    ) -> None:
        """Set (reset) a user's password.

        `temporary=False` makes it a permanent credential the user can log in with
        immediately (used by self-service signup, where the founder picks their own
        password). Keycloak enforces the realm password policy here and returns 400
        if the password is rejected — callers map that to a clean WEAK_PASSWORD.
        """
        await self._request(
            "PUT",
            f"/users/{user_id}/reset-password",
            json={"type": "password", "value": password, "temporary": temporary},
            expect={204},
        )

    async def assign_realm_role(self, user_id: str, role_name: str) -> None:
        """Grant a realm role to a user."""
        role = (await self._request("GET", f"/roles/{role_name}")).json()
        await self._request(
            "POST",
            f"/users/{user_id}/role-mappings/realm",
            json=[{"id": role["id"], "name": role["name"]}],
            expect={204},
        )

    async def send_execute_actions_email(
        self,
        user_id: str,
        actions: list[str],
        *,
        client_id: str | None = None,
        redirect_uri: str | None = None,
        lifespan_seconds: int | None = None,
    ) -> None:
        """Email the user a link to perform required actions (e.g. set password).

        Common actions: "UPDATE_PASSWORD", "VERIFY_EMAIL". Requires SMTP configured
        on the realm; otherwise Keycloak returns an error.
        """
        params: dict[str, str] = {}
        if client_id is not None:
            params["client_id"] = client_id
        if redirect_uri is not None:
            params["redirect_uri"] = redirect_uri
        if lifespan_seconds is not None:
            params["lifespan"] = str(lifespan_seconds)
        await self._request(
            "PUT",
            f"/users/{user_id}/execute-actions-email",
            json=actions,
            params=params or None,
            expect={204},
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> KeycloakAdminClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()


@dataclass(slots=True)
class TenantGroupSpec:
    """Input for provisioning a Keycloak group for one tenant."""

    tenant_slug: str
    tenant_id: str  # UUID as string — becomes a group attribute so token mappers can embed it

    @property
    def group_name(self) -> str:
        return f"tenant_{self.tenant_slug}"

    @property
    def attributes(self) -> dict[str, list[str]]:
        return {"tenant_id": [self.tenant_id]}
