"""Audit application service — read-only query surface.

The service is intentionally thin: the audit module has no write-side
orchestration because all writes go through the AuditEventSubscriber
(triggered by the EventBus). This service only exposes filtered, paged
reads for the API layer.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.modules.audit.application.dtos import AuditEntriesPage, ListAuditEntriesQuery
from app.modules.audit.application.ports import AuditRepository


@dataclass(slots=True)
class AuditService:
    """Orchestrates read queries against the audit repository."""

    repo: AuditRepository

    async def list(self, *, query: ListAuditEntriesQuery) -> AuditEntriesPage:
        items, total = await self.repo.list_entries(
            entity_type=query.entity_type,
            entity_id=query.entity_id,
            action=query.action,
            actor=query.actor,
            limit=query.limit,
            offset=query.offset,
        )
        return AuditEntriesPage(
            items=items,
            total=total,
            limit=query.limit,
            offset=query.offset,
        )
