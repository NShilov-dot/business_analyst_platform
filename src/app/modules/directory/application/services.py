"""Directory application service.

Thin orchestration layer: delegates to the DirectoryReader port and returns
DTOs.  No framework imports — depends only on ports and DTOs.
"""

from __future__ import annotations

from app.modules.directory.application.dtos import DirectoryUser, ListDirectoryUsersQuery
from app.modules.directory.application.ports import DirectoryReader


class DirectoryService:
    """Orchestrates user-directory queries for the caller's tenant."""

    def __init__(self, reader: DirectoryReader) -> None:
        self._reader = reader

    async def list_users(
        self,
        *,
        tenant_id: str,
        query: ListDirectoryUsersQuery,
    ) -> list[DirectoryUser]:
        """Return tenant users matching the query.

        Args:
            tenant_id: UUID string of the caller's tenant (from verified token).
            query:     Validated query parameters (search string, limit).
        """
        return await self._reader.list_tenant_users(
            tenant_id=tenant_id,
            search=query.search,
            limit=query.limit,
        )
