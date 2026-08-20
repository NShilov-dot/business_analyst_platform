"""Adapters wiring ai_structuring's outward ports to sibling modules."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ai_structuring.domain.entities import DocumentText
from app.modules.documents.domain.ports import DocumentRepository
from app.modules.intake_templates.domain.entities import (
    FieldDefinition,
    TemplateVersionStatus,
)
from app.modules.intake_templates.infrastructure.repositories import (
    SqlAlchemyTemplateRepository,
)
from app.modules.tickets.application.dtos import CreateTicketCommand, SubmitCommand
from app.modules.tickets.application.services import TicketService

# Mirrors DocumentService's own visibility rule (owner or ba/tenant_admin/
# platform_admin) rather than depending on the full DocumentService, which
# also carries an object store + text extractor this read-only seam never
# uses. If that rule changes, update both.
_READ_ANY_ROLES = frozenset({"ba", "tenant_admin", "platform_admin"})


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


class DocumentServiceTextProvider:
    """DocumentTextProvider over the documents module's repository (mirrors
    SqlTemplateFieldsProvider). Access-filtered per document: owner or
    ba/tenant_admin/platform_admin, same as a direct read through `documents`.
    """

    def __init__(self, repo: DocumentRepository) -> None:
        self._repo = repo

    async def get_texts_for_session(
        self,
        document_ids: tuple[UUID, ...],
        *,
        requester_id: UUID,
        roles: frozenset[str],
    ) -> tuple[DocumentText, ...]:
        texts: list[DocumentText] = []
        for document_id in document_ids:
            document = await self._repo.get_by_id(document_id)
            if document is None:
                continue
            if document.owner_id != requester_id and not (roles & _READ_ANY_ROLES):
                continue
            texts.append(
                DocumentText(
                    id=document.id,
                    filename=document.filename,
                    status=document.status.value,
                    text=document.extracted_text or "",
                )
            )
        return tuple(texts)


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
