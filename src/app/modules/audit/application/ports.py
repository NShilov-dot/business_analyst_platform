"""Ports (abstract contracts) for the audit application layer.

Concrete implementations live in `infrastructure/repositories.py`.
The application service depends only on these protocols — not on SQLAlchemy.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.modules.audit.application.dtos import AppendAuditEntryCommand, AuditEntry


class AuditRepository(Protocol):
    """Read/write contract for audit entry persistence."""

    async def append(self, cmd: AppendAuditEntryCommand) -> None:
        """Persist one audit entry and flush; never commit."""
        ...

    async def list_entries(
        self,
        *,
        entity_type: str | None = None,
        entity_id: UUID | None = None,
        action: str | None = None,
        actor: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[AuditEntry], int]:
        """Return a filtered, paged list of entries and the total count."""
        ...
