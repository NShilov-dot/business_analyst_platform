"""Keycloak-backed implementation of the DirectoryReader port.

Queries the Keycloak Admin REST API to list users by tenant_id attribute,
optionally filtered by a name/username/email search string.  Service-account
users (username prefix ``service-account-``) are excluded from results.
"""

from __future__ import annotations

import structlog

from app.core.keycloak_admin import KeycloakAdminClient
from app.modules.directory.application.dtos import DirectoryUser
from app.modules.directory.application.ports import DirectoryReader

logger = structlog.get_logger(__name__)

_SERVICE_ACCOUNT_PREFIX = "service-account-"


class KeycloakDirectoryReader:
    """DirectoryReader that fetches users from Keycloak.

    Implements the DirectoryReader protocol; registered at the interface layer
    per-request (no shared mutable state beyond the injected client).
    """

    def __init__(self, client: KeycloakAdminClient) -> None:
        self._client = client

    async def list_tenant_users(
        self,
        *,
        tenant_id: str,
        search: str | None,
        limit: int,
    ) -> list[DirectoryUser]:
        """Fetch up to `limit` tenant members from Keycloak, excluding service accounts."""
        raw_users = await self._client.search_users(
            q=f"tenant_id:{tenant_id}",
            search=search or None,
            enabled=True,  # never surface deprovisioned / suspended accounts in a picker
            max_results=limit,
        )
        results: list[DirectoryUser] = []
        for raw in raw_users:
            username = str(raw.get("username", ""))
            if username.startswith(_SERVICE_ACCOUNT_PREFIX):
                continue
            first_name: str | None = raw.get("firstName")
            last_name: str | None = raw.get("lastName")
            email: str | None = raw.get("email")
            full_name = (f"{first_name or ''} {last_name or ''}".strip()) or username
            results.append(
                DirectoryUser(
                    subject=str(raw["id"]),
                    username=username,
                    first_name=first_name,
                    last_name=last_name,
                    full_name=full_name,
                    email=email,
                )
            )
        return results


# Satisfy the Protocol at import time so mypy catches drift early.
_: DirectoryReader = KeycloakDirectoryReader.__new__(KeycloakDirectoryReader)
