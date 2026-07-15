"""SQLAlchemy-backed repository for audit entries.

Callers own the transaction boundary (SessionDep); this adapter calls
`flush()` but NEVER `commit()`.

The `append()` method is the ONLY write path — nothing else writes to
`audit_entries` directly.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.application.dtos import AppendAuditEntryCommand, AuditEntry
from app.modules.audit.infrastructure.models import AuditEntryRow


def _to_entry(row: AuditEntryRow) -> AuditEntry:
    return AuditEntry(
        id=row.id,
        entity_type=row.entity_type,
        entity_id=row.entity_id,
        action=row.action,
        actor=row.actor,
        roles=list(row.roles) if row.roles is not None else [],
        before=row.before,
        after=row.after,
        request_id=row.request_id,
        occurred_at=row.occurred_at,
    )


class SqlAlchemyAuditRepository:
    """Concrete audit repository. Session is caller-owned."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(self, cmd: AppendAuditEntryCommand) -> None:
        """Insert one audit row and flush to the session (no commit)."""
        row = AuditEntryRow(
            id=cmd.id,
            entity_type=cmd.entity_type,
            entity_id=cmd.entity_id,
            action=cmd.action,
            actor=cmd.actor,
            roles=cmd.roles or [],
            before=cmd.before,
            after=cmd.after,
            request_id=cmd.request_id,
            occurred_at=cmd.occurred_at,
        )
        self._session.add(row)
        await self._session.flush()

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
        q = select(AuditEntryRow)
        count_q = select(func.count()).select_from(AuditEntryRow)

        if entity_type is not None:
            q = q.where(AuditEntryRow.entity_type == entity_type)
            count_q = count_q.where(AuditEntryRow.entity_type == entity_type)
        if entity_id is not None:
            q = q.where(AuditEntryRow.entity_id == entity_id)
            count_q = count_q.where(AuditEntryRow.entity_id == entity_id)
        if action is not None:
            q = q.where(AuditEntryRow.action == action)
            count_q = count_q.where(AuditEntryRow.action == action)
        if actor is not None:
            q = q.where(AuditEntryRow.actor == actor)
            count_q = count_q.where(AuditEntryRow.actor == actor)

        q = q.order_by(AuditEntryRow.occurred_at.desc()).limit(limit).offset(offset)

        rows = (await self._session.scalars(q)).all()
        total = await self._session.scalar(count_q) or 0
        return [_to_entry(r) for r in rows], total
