"""Adapters wiring ai_structuring's outward ports to sibling modules."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.intake_templates.domain.entities import (
    FieldDefinition,
    TemplateVersionStatus,
)
from app.modules.intake_templates.infrastructure.repositories import (
    SqlAlchemyTemplateRepository,
)
from app.modules.tickets.application.dtos import CreateTicketCommand, SubmitCommand
from app.modules.tickets.application.services import TicketService


class SqlTemplateFieldsProvider:
    """TemplateFieldsProvider over the tenant's template_versions table."""

    def __init__(self, session: AsyncSession) -> None:
        self._repo = SqlAlchemyTemplateRepository(session)

    async def get_published_fields(
        self, version_id: UUID
    ) -> tuple[FieldDefinition, ...] | None:
        version = await self._repo.get_version_by_id(version_id)
        if version is None or version.status != TemplateVersionStatus.PUBLISHED:
            return None
        return version.fields


class TicketServiceIntakeSink:
    """TicketIntakeSink over the tickets module.

    create + submit run in the SAME request transaction as the chat session
    update, with the human requester as actor — the resulting rows, events and
    audit trail are indistinguishable from a manually filed ticket.
    """

    def __init__(self, tickets: TicketService) -> None:
        self._tickets = tickets

    async def create_and_submit(
        self,
        *,
        actor_id: UUID,
        actor_sub: str,
        roles: frozenset[str],
        title: str,
        template_version_id: UUID,
        payload: dict[str, object],
    ) -> UUID:
        ticket, _, _ = await self._tickets.create_ticket(
            actor_id=actor_id,
            actor_sub=actor_sub,
            roles=roles,
            command=CreateTicketCommand(
                title=title,
                template_version_id=template_version_id,
                payload=payload,
            ),
        )
        await self._tickets.submit(
            ticket_id=ticket.id,
            actor_id=actor_id,
            actor_sub=actor_sub,
            roles=roles,
            command=SubmitCommand(),
        )
        return ticket.id
