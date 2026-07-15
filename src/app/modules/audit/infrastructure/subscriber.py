"""Domain event subscriber that writes audit log entries.

This is the ONLY path that writes to `audit_entries`.  Publishing a domain
event IS the act of auditing — nothing writes to the table directly.

Fail-closed by design: if the audit write fails (e.g. DB constraint), the
exception propagates through the EventBus and the surrounding request
transaction is rolled back.  This satisfies §8 completeness: a mutation that
cannot be audited MUST NOT be committed.
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import DomainEvent
from app.modules.audit.application.dtos import AppendAuditEntryCommand
from app.modules.audit.infrastructure.repositories import SqlAlchemyAuditRepository


class AuditEventSubscriber:
    """Maps every DomainEvent to an AuditEntry and appends it in the same session."""

    async def __call__(self, event: DomainEvent, session: AsyncSession) -> None:
        repo = SqlAlchemyAuditRepository(session)
        cmd = AppendAuditEntryCommand(
            id=uuid4(),
            entity_type=event.entity_type,
            entity_id=event.entity_id,
            action=event.action,
            actor=event.actor,
            roles=list(event.roles),
            before=event.before,
            after=event.after,
            request_id=event.request_id,
            occurred_at=event.occurred_at,
        )
        await repo.append(cmd)
