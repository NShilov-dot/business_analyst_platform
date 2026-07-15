"""Port contracts for the ai_structuring module.

All ports are typing.Protocol — framework-free.

Deliberately ABSENT (docs/PRODUCT_MODULES.md §4.9 human-control-by-construction):
there is no port for ticket transitions or gate approvals.  The only outward
capability is ``TicketIntakeSink.create_and_submit`` — the same audited human
command the manual intake form triggers, executed with the human requester as
the acting Principal.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.modules.ai_structuring.domain.entities import ChatMessage, ChatSession, LlmTurn
from app.modules.intake_templates.domain.entities import FieldDefinition


class ChatSessionRepository(Protocol):
    """Persistence port. Repositories flush(), never commit()."""

    async def add_session(self, session: ChatSession) -> None: ...

    async def get_session_by_id(self, session_id: UUID) -> ChatSession | None: ...

    async def update_session(self, session: ChatSession) -> None: ...

    async def list_sessions_for_requester(
        self, requester_id: UUID, *, limit: int, offset: int
    ) -> tuple[list[ChatSession], int]: ...

    async def add_message(self, message: ChatMessage) -> None: ...

    async def list_messages(self, session_id: UUID) -> list[ChatMessage]: ...


class LlmPort(Protocol):
    """One structured interview turn against the LLM provider.

    ``history`` is the full ordered conversation (role, content) INCLUDING the
    new user message.  The adapter owns transport, retries and JSON parsing;
    it raises LlmUnavailableError on provider failure.
    """

    async def complete_turn(
        self,
        *,
        system_prompt: str,
        history: list[tuple[str, str]],
    ) -> LlmTurn: ...


class TemplateFieldsProvider(Protocol):
    """Thin seam: published template version's field definitions for prompting."""

    async def get_published_fields(
        self, version_id: UUID
    ) -> tuple[FieldDefinition, ...] | None:
        """Field definitions, or None when the version is missing/unpublished."""
        ...


class TicketIntakeSink(Protocol):
    """Create a ticket draft from the consolidated payload and submit it to triage.

    Implemented over TicketService — every effect (rows, transitions, domain
    events, audit) is identical to a manually filed ticket.
    """

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
        """Returns the created ticket id (status = triage)."""
        ...


class Clock(Protocol):
    def __call__(self) -> datetime: ...
