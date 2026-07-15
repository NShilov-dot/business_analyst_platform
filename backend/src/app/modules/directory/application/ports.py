"""Port (protocol) contracts for the directory module.

Infrastructure adapters implement these protocols so the application layer
stays free of framework / external-service imports.
"""

from __future__ import annotations

from typing import Protocol

from app.modules.directory.application.dtos import DirectoryUser


class DirectoryReader(Protocol):
    """Read interface for fetching tenant-scoped user listings."""

    async def list_tenant_users(
        self,
        *,
        tenant_id: str,
        search: str | None,
        limit: int,
    ) -> list[DirectoryUser]:
        """Return up to `limit` users belonging to `tenant_id`.

        Args:
            tenant_id: The tenant UUID string (from the verified principal).
            search:    Optional prefix search across name/username/email.
            limit:     Maximum number of users to return.
        """
        ...
