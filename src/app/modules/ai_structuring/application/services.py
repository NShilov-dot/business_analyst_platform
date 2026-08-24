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

import logging
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
from app.modules.ai_structuring.application.prompts import (
    build_documents_analysis_prompt,
    build_system_prompt,
    coerce_draft,
)
from app.modules.ai_structuring.domain.entities import (
    ANALYSIS_PENDING_TTL_SECONDS,
    DOC_TEXT_INPUT_CAP,
    ChatAnalysisStatus,
    ChatMessage,
    ChatRole,
    ChatSession,
    ChatSessionStatus,
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
    DocumentTextProvider,
    LlmPort,
    TemplateFieldsProvider,
    TicketIntakeSink,
)
from app.modules.intake_templates.domain.entities import FieldDefinition
from app.modules.intake_templates.domain.ports import SubmissionValidator

_TITLE_MAX = 200

log = logging.getLogger(__name__)


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
    doc_texts: DocumentTextProvider
    clock: Clock = field(default=_utc_now)

    # ================================================================== #
    # Session lifecycle                                                   #
    # ================================================================== #

    async def start_session(
        self,
        *,
        actor_id: UUID,
        actor_sub: str,
        roles: frozenset[str],
        command: StartSessionCommand,
    ) -> SessionDetail:
        """Create the session and return FAST — the expensive LLM document
        pre-analysis, if any, is deferred to a background task (see
        run_document_analysis) scheduled by the router after this returns.

        With document_ids: a cheap synchronous pre-flight (link + confirm at
        least one document is usable) still runs here so 403/422 surface in
        THIS request; analysis_status flips to 'pending' and the caller polls
        get_session(). Without document_ids: analysis_status stays 'none' and
        behavior is unchanged (immediate active session, no analysis).
        """
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
                "document_count": len(command.document_ids),
            },
        )

        if command.document_ids:
            await self._link_and_validate_documents(
                session, command.document_ids, actor_id=actor_id, roles=roles
            )
            await self.repo.update_session(session)

        states, is_ready = await self._field_states(session, fields)
        return SessionDetail(session=session, messages=[], fields=states, is_ready=is_ready)

    async def _link_and_validate_documents(
        self,
        session: ChatSession,
        document_ids: tuple[UUID, ...],
        *,
        actor_id: UUID,
        roles: frozenset[str],
    ) -> None:
        """Synchronous pre-flight run inside start_session, BEFORE the
        expensive LLM pass is deferred: link the documents and confirm at
        least one is usable, so access/validation errors still surface as an
        immediate 403/422 rather than only showing up once polled. On success,
        flips analysis_status to 'pending' — the actual LLM call happens later
        in run_document_analysis().
        """
        await self.repo.link_documents(session.id, document_ids)
        texts = await self.doc_texts.get_texts_for_session(
            document_ids, requester_id=actor_id, roles=roles
        )
        if len(texts) < len(document_ids):
            raise ChatAccessDeniedError(
                "One or more attached documents are not accessible to this account"
            )

        usable = [t for t in texts if t.status == "extracted" and t.text.strip()]
        if not usable:
            raise ChatValidationError(
                "Ни один из приложенных документов не готов к анализу "
                "(извлечение текста не удалось или файл пуст)"
            )

        session.mark_analysis_pending(now=self.clock())

    async def run_document_analysis(
        self,
        *,
        session_id: UUID,
        document_ids: tuple[UUID, ...],
        actor_id: UUID,
        roles: frozenset[str],
    ) -> None:
        """Deferred work scheduled by the router right after start_session
        returns — the actual LLM pass over the attached documents.

        Idempotent: a missing/inactive session, or one whose analysis_status
        is no longer 'pending' (already applied, failed, or the session was
        discarded/submitted meanwhile), is a no-op — safe to retry.

        NEVER raises: any failure (LLM down, documents vanished, template
        unpublished in the meantime, ...) flips analysis_status to 'failed'
        and is logged, so a background task exception never surfaces to a
        caller that isn't there to see it. The session stays usable — the
        requester can still type; documents_context/draft/opener simply
        remain unset.
        """
        session = await self.repo.get_session_by_id(session_id)
        if (
            session is None
            or session.status != ChatSessionStatus.ACTIVE
            or session.analysis_status != ChatAnalysisStatus.PENDING
        ):
            return

        try:
            fields = await self._published_fields_or_422(session.template_version_id)
            texts = await self.doc_texts.get_texts_for_session(
                document_ids, requester_id=actor_id, roles=roles
            )
            usable = [t for t in texts if t.status == "extracted" and t.text.strip()]
            combined = "\n\n".join(f"# {t.filename}\n{t.text}" for t in usable)[
                :DOC_TEXT_INPUT_CAP
            ]
            analysis = await self.llm.analyze_documents(
                system_prompt=build_documents_analysis_prompt(fields), text=combined
            )
            prefilled = coerce_draft(dict(analysis.draft), fields)

            now = self.clock()
            session.open_with_documents(
                analysis.summary,
                draft=prefilled,
                prefilled_keys=tuple(prefilled.keys()),
                now=now,
            )
            opener = ChatMessage(
                id=uuid4(),
                session_id=session.id,
                seq=1,
                role=ChatRole.ASSISTANT,
                content=analysis.opening,
                created_at=now,
            )
            await self.repo.add_message(opener)
            await self.repo.update_session(session)
            await self.publisher(
                "ai_chat_session",
                session.id,
                "analysis_completed",
                after={
                    "message_count": session.message_count,
                    "prefilled_count": len(prefilled),
                },
            )
        except Exception as exc:  # background work must not raise
            log.warning(
                "ai_chat.analysis_failed session_id=%s error=%s", session_id, exc
            )
            session.mark_analysis_failed(now=self.clock())
            await self.repo.update_session(session)
            await self.publisher(
                "ai_chat_session",
                session.id,
                "analysis_failed",
                after={"reason": type(exc).__name__},
            )

    async def get_session(
        self, *, session_id: UUID, actor_id: UUID, roles: frozenset[str]
    ) -> SessionDetail:
        session = await self._load_owned(session_id, actor_id=actor_id, roles=roles)
        # Self-heal an orphaned analysis: the background task is in-process and
        # dies with a restart without flipping the status, so a stale 'pending'
        # would otherwise poll (and block turns/finalize) forever.
        now = self.clock()
        if (
            session.analysis_status == ChatAnalysisStatus.PENDING
            and (now - session.updated_at).total_seconds() > ANALYSIS_PENDING_TTL_SECONDS
        ):
            session.mark_analysis_failed(now=now)
            await self.repo.update_session(session)
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
        session.assert_not_analyzing()
        content = validate_message_content(command.content)
        fields = await self._published_fields_or_422(session.template_version_id)

        user_seq, assistant_seq = session.next_seq_pair()
        now = self.clock()

        history = [
            (m.role.value, m.content) for m in await self.repo.list_messages(session_id)
        ]
        history.append((ChatRole.USER.value, content))

        turn = await self.llm.complete_turn(
            system_prompt=build_system_prompt(fields, documents_context=session.documents_context),
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
        session.assert_not_analyzing()

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
        # Author the ticket as the session's REQUESTER, not the caller. A BA or
        # admin may finalize on the requester's behalf (allowed by _load_owned),
        # but the resulting ticket's author must be the requester — the double-
        # acceptance value gate and requester-scoped analytics depend on it. Roles
        # are the requester's baseline (any-member), NOT the caller's: the ticket
        # is filed AS the requester, so a BA's elevated roles must not tag along.
        ticket_id = await self.ticket_sink.create_and_submit(
            actor_id=session.requester_id,
            actor_sub=session.requester_sub,
            roles=frozenset({"tenant_user"}),
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

    async def rename(
        self, *, session_id: UUID, actor_id: UUID, roles: frozenset[str], title: str
    ) -> ChatSession:
        session = await self._load_owned(session_id, actor_id=actor_id, roles=roles)
        session.rename(title, now=self.clock())
        await self.repo.update_session(session)
        # PII discipline: never put the title text in the event payload.
        await self.publisher(
            "ai_chat_session", session_id, "renamed", after={"has_title": True}
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
        prefilled = set(session.documents_prefilled_keys)
        states = [
            FieldState(
                key=f.key,
                label=f.label,
                required=f.required,
                value=(str(v) if (v := session.draft.get(f.key)) is not None else None),
                missing=f.key in failing,
                from_document=f.key in prefilled,
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
