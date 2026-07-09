"""ChatIntakeService — orchestrates the AI-assisted intake conversation.

Context storage: every session's full message history + consolidated draft
live in tenant-scoped Postgres tables; each turn reloads the history and sends
it to the LLM, so sessions survive restarts and are resumable from any device.

PII discipline: domain events carry ids/statuses/counters — never chat text
or draft field values.

Human control: finalize() is the ONLY path to a ticket, it requires the human
requester as actor, and it revalidates the draft with the same
SubmissionValidator as the manual form before calling the tickets module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.core.events import EventPublisher
from app.modules.ai_structuring.application.dtos import (
    FieldState,
    SendMessageCommand,
    SessionDetail,
    SessionPage,
    StartSessionCommand,
    TurnResult,
)
from app.modules.ai_structuring.application.prompts import build_system_prompt, coerce_draft
from app.modules.ai_structuring.domain.entities import (
    ChatMessage,
    ChatRole,
    ChatSession,
    validate_message_content,
)
from app.modules.ai_structuring.domain.errors import (
    ChatAccessDeniedError,
    ChatSessionNotFoundError,
    ChatValidationError,
    DraftIncompleteError,
)
from app.modules.ai_structuring.domain.ports import (
    ChatSessionRepository,
    Clock,
    LlmPort,
    TemplateFieldsProvider,
    TicketIntakeSink,
)
from app.modules.intake_templates.domain.entities import FieldDefinition
from app.modules.intake_templates.domain.ports import SubmissionValidator

_TITLE_MAX = 200


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class ChatIntakeService:
    repo: ChatSessionRepository
    llm: LlmPort
    fields_provider: TemplateFieldsProvider
    validator: SubmissionValidator
    ticket_sink: TicketIntakeSink
    publisher: EventPublisher
    clock: Clock = field(default=_utc_now)

    # ================================================================== #
    # Session lifecycle                                                   #
    # ================================================================== #

    async def start_session(
        self,
        *,
        actor_id: UUID,
        actor_sub: str,
        command: StartSessionCommand,
    ) -> SessionDetail:
        fields = await self._published_fields_or_422(command.template_version_id)
        now = self.clock()

        session = ChatSession.start(
            requester_id=actor_id,
            requester_sub=actor_sub,
            template_version_id=command.template_version_id,
            now=now,
        )
        await self.repo.add_session(session)

        await self.publisher(
            "ai_chat_session",
            session.id,
            "started",
            after={
                "status": session.status,
                "template_version_id": str(command.template_version_id),
            },
        )
        states, is_ready = await self._field_states(session, fields)
        return SessionDetail(session=session, messages=[], fields=states, is_ready=is_ready)

    async def get_session(
        self, *, session_id: UUID, actor_id: UUID, roles: frozenset[str]
    ) -> SessionDetail:
        session = await self._load_owned(session_id, actor_id=actor_id, roles=roles)
        fields = await self._fields_or_empty(session.template_version_id)
        messages = await self.repo.list_messages(session_id)
        states, is_ready = await self._field_states(session, fields)
        return SessionDetail(session=session, messages=messages, fields=states, is_ready=is_ready)

    async def list_sessions(
        self, *, actor_id: UUID, limit: int, offset: int
    ) -> SessionPage:
        items, total = await self.repo.list_sessions_for_requester(
            actor_id, limit=limit, offset=offset
        )
        return SessionPage(items=items, total=total, limit=limit, offset=offset)

    # ================================================================== #
    # Conversation turn                                                   #
    # ================================================================== #

    async def send_message(
        self,
        *,
        session_id: UUID,
        actor_id: UUID,
        roles: frozenset[str],
        command: SendMessageCommand,
    ) -> TurnResult:
        session = await self._load_owned(session_id, actor_id=actor_id, roles=roles)
        session.assert_active()
        content = validate_message_content(command.content)
        fields = await self._published_fields_or_422(session.template_version_id)

        user_seq, assistant_seq = session.next_seq_pair()
        now = self.clock()

        history = [
            (m.role.value, m.content) for m in await self.repo.list_messages(session_id)
        ]
        history.append((ChatRole.USER.value, content))

        turn = await self.llm.complete_turn(
            system_prompt=build_system_prompt(fields),
            history=history,
        )

        user_message = ChatMessage(
            id=uuid4(),
            session_id=session_id,
            seq=user_seq,
            role=ChatRole.USER,
            content=content,
            created_at=now,
        )
        assistant_message = ChatMessage(
            id=uuid4(),
            session_id=session_id,
            seq=assistant_seq,
            role=ChatRole.ASSISTANT,
            content=turn.reply,
            created_at=now,
        )
        await self.repo.add_message(user_message)
        await self.repo.add_message(assistant_message)

        session.record_turn(
            draft=coerce_draft(dict(turn.draft), fields),
            title=turn.title,
            now=now,
        )
        await self.repo.update_session(session)

        states, is_ready = await self._field_states(session, fields)

        await self.publisher(
            "ai_chat_session",
            session_id,
            "turn_recorded",
            after={
                "message_count": session.message_count,
                "draft_ready": is_ready,
            },
        )
        return TurnResult(
            session=session, reply=assistant_message, fields=states, is_ready=is_ready
        )

    # ================================================================== #
    # Finalize — the human sends the consolidated draft to work           #
    # ================================================================== #

    async def finalize(
        self,
        *,
        session_id: UUID,
        actor_id: UUID,
        actor_sub: str,
        roles: frozenset[str],
    ) -> SessionDetail:
        session = await self._load_owned(session_id, actor_id=actor_id, roles=roles)
        session.assert_active()

        errors = await self.validator.validate_submission(
            template_version_id=session.template_version_id,
            payload=session.draft,
        )
        if errors:
            raise DraftIncompleteError(
                "Draft does not satisfy the template yet",
                field_errors=[{"key": e.key, "message": e.message} for e in errors],
            )

        title = self._derive_title(session)
        ticket_id = await self.ticket_sink.create_and_submit(
            actor_id=actor_id,
            actor_sub=actor_sub,
            roles=roles,
            title=title,
            template_version_id=session.template_version_id,
            payload=session.draft,
        )

        now = self.clock()
        session.mark_submitted(ticket_id=ticket_id, now=now)
        await self.repo.update_session(session)

        await self.publisher(
            "ai_chat_session",
            session_id,
            "finalized",
            after={
                "status": session.status,
                "ticket_id": str(ticket_id),
                "message_count": session.message_count,
            },
        )
        fields = await self._fields_or_empty(session.template_version_id)
        messages = await self.repo.list_messages(session_id)
        states, is_ready = await self._field_states(session, fields)
        return SessionDetail(session=session, messages=messages, fields=states, is_ready=is_ready)

    async def discard(
        self, *, session_id: UUID, actor_id: UUID, roles: frozenset[str]
    ) -> ChatSession:
        session = await self._load_owned(session_id, actor_id=actor_id, roles=roles)
        session.discard(now=self.clock())
        await self.repo.update_session(session)
        await self.publisher(
            "ai_chat_session",
            session_id,
            "discarded",
            after={"status": session.status},
        )
        return session

    # ================================================================== #
    # Internal helpers                                                    #
    # ================================================================== #

    async def _load_owned(
        self, session_id: UUID, *, actor_id: UUID, roles: frozenset[str]
    ) -> ChatSession:
        session = await self.repo.get_session_by_id(session_id)
        if session is None:
            raise ChatSessionNotFoundError(f"Chat session {session_id} not found")
        # The conversation may contain the requester's free text — owner-only,
        # with ba/admin read allowed via the same rule as ticket management.
        if session.requester_id != actor_id and not (
            roles & {"ba", "tenant_admin", "platform_admin"}
        ):
            raise ChatAccessDeniedError("Only the session owner may access this chat")
        return session

    async def _published_fields_or_422(
        self, version_id: UUID
    ) -> tuple[FieldDefinition, ...]:
        fields = await self.fields_provider.get_published_fields(version_id)
        if fields is None:
            raise ChatValidationError(
                f"Template version {version_id} is not published; "
                "AI intake requires a published template version"
            )
        return fields

    async def _fields_or_empty(self, version_id: UUID) -> tuple[FieldDefinition, ...]:
        return await self.fields_provider.get_published_fields(version_id) or ()

    async def _field_states(
        self, session: ChatSession, fields: tuple[FieldDefinition, ...]
    ) -> tuple[list[FieldState], bool]:
        errors = await self.validator.validate_submission(
            template_version_id=session.template_version_id,
            payload=session.draft,
        )
        failing = {e.key for e in errors}
        states = [
            FieldState(
                key=f.key,
                label=f.label,
                required=f.required,
                value=(str(v) if (v := session.draft.get(f.key)) is not None else None),
                missing=f.key in failing,
            )
            for f in fields
        ]
        return states, not errors

    def _derive_title(self, session: ChatSession) -> str:
        title = (session.draft_title or "").strip()
        if not title:
            problem = str(session.draft.get("problem", "")).strip()
            title = problem or "Заявка из ИИ-интейка"
        if len(title) > _TITLE_MAX:
            title = title[: _TITLE_MAX - 1] + "…"
        return title
