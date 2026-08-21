"""ai_structuring domain entities: the intake chat session.

No SQLAlchemy / Pydantic / FastAPI imports allowed in this module.

Human-control invariant (docs/PRODUCT_MODULES.md §4.9): this module produces
DRAFTS only.  A session never transitions a ticket — finalization is a human
command that flows through the tickets module's normal audited path, with the
human requester as the acting Principal.  The AI's completeness claim is
advisory; the authoritative readiness check is the same SubmissionValidator
the manual intake form uses.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from app.modules.ai_structuring.domain.errors import (
    ChatSessionClosedError,
    ChatValidationError,
)

MESSAGE_MAX = 8_000
# Hard cap on stored turns per session — bounds both the DB row count and the
# prompt size sent to the LLM provider.
MAX_MESSAGES_PER_SESSION = 200

# Documents pre-analysis (see application/services.py start_session). The
# summary is stored on the session and re-sent to the LLM EVERY turn, so it
# stays small; the raw combined document text is only ever sent ONCE, at
# session start, and is capped purely to bound cost/latency of that one call.
SUMMARY_CHAR_CAP = 4_000
DOC_TEXT_INPUT_CAP = 60_000


class ChatSessionStatus(StrEnum):
    ACTIVE = "active"
    SUBMITTED = "submitted"  # ticket created & sent to triage by the human
    DISCARDED = "discarded"


class ChatAnalysisStatus(StrEnum):
    """Lifecycle of the deferred, background document pre-analysis.

    ``NONE`` — no documents attached at start, nothing to analyze.
    ``PENDING`` — documents linked and validated synchronously; the LLM pass
    is scheduled/running in a background task (see
    ChatIntakeService.run_document_analysis). The frontend polls
    GET /sessions/{id} while in this state.
    ``READY`` — analysis applied: documents_context/draft/opener are set.
    ``FAILED`` — the background pass raised; the session stays usable without
    documents context (draft/opener remain empty, no retry is scheduled).
    """

    NONE = "none"
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"


class ChatRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(slots=True, kw_only=True)
class ChatMessage:
    """One turn of the intake conversation (append-only)."""

    id: UUID
    session_id: UUID
    seq: int  # per-session counter, 1..N — stable ordering independent of clock
    role: ChatRole
    content: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class LlmTurn:
    """Parsed result of one LLM completion.

    ``draft`` maps template field keys to collected string values.
    ``title`` is the LLM's running suggestion for the ticket title.
    ``complete`` is the model's ADVISORY claim — never trusted directly.
    """

    reply: str
    draft: dict[str, str]
    title: str | None
    complete: bool


@dataclass(frozen=True, slots=True)
class DocumentPreAnalysis:
    """Result of the ONE-TIME `analyze_documents` LLM pass at session start.

    ``summary`` is stored on the session and mixed into the interview prompt
    every turn; ``opening`` becomes the lone assistant message at seq=1;
    ``draft`` pre-fills the draft fields the documents already answer (keyed by
    template field key, same discipline as ``LlmTurn.draft``).
    """

    summary: str
    opening: str
    draft: dict[str, str]


@dataclass(frozen=True, slots=True)
class DocumentText:
    """Extracted text for one document attached at session start, as seen
    through the access-filtered DocumentTextProvider port."""

    id: UUID
    filename: str
    status: str  # documents.domain.entities.DocumentStatus value
    text: str


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True, kw_only=True)
class ChatSession:
    """Aggregate root for one AI-assisted intake conversation.

    ``draft`` is the consolidated intake payload accumulated over the chat.
    Each assistant turn is MERGED over it (not replaced): the LLM authors every
    key it emits, but values already collected — notably fields pre-filled from
    attached documents — survive a turn where the model omits them. All values
    are filtered to declared template keys by the application service.
    ``documents_prefilled_keys`` records which keys were seeded from the
    attached documents (drives the "из документа" marker in the draft panel).
    """

    id: UUID
    requester_id: UUID
    requester_sub: str
    template_version_id: UUID
    status: ChatSessionStatus
    analysis_status: ChatAnalysisStatus
    draft: dict[str, object]
    draft_title: str | None
    documents_context: str | None
    documents_prefilled_keys: tuple[str, ...]
    message_count: int
    ticket_id: UUID | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def start(
        cls,
        *,
        requester_id: UUID,
        requester_sub: str,
        template_version_id: UUID,
        now: datetime | None = None,
    ) -> ChatSession:
        ts = now or _now()
        return cls(
            id=uuid4(),
            requester_id=requester_id,
            requester_sub=requester_sub,
            template_version_id=template_version_id,
            status=ChatSessionStatus.ACTIVE,
            analysis_status=ChatAnalysisStatus.NONE,
            draft={},
            draft_title=None,
            documents_context=None,
            documents_prefilled_keys=(),
            message_count=0,
            ticket_id=None,
            created_at=ts,
            updated_at=ts,
        )

    # ------------------------------------------------------------------ #
    # Guards & transitions                                                #
    # ------------------------------------------------------------------ #

    def assert_active(self) -> None:
        if self.status != ChatSessionStatus.ACTIVE:
            raise ChatSessionClosedError(
                f"Chat session is {self.status!s}; only active sessions accept messages"
            )

    def assert_not_analyzing(self) -> None:
        """Block conversation turns/finalize while the background document
        pre-analysis is still in flight — the opener/context/draft it seeds
        aren't there yet."""
        if self.analysis_status == ChatAnalysisStatus.PENDING:
            raise ChatValidationError(
                "Документы ещё анализируются — дождитесь завершения"
            )

    def next_seq_pair(self) -> tuple[int, int]:
        """Sequence numbers for the next (user, assistant) message pair."""
        if self.message_count + 2 > MAX_MESSAGES_PER_SESSION:
            raise ChatValidationError(
                f"Chat session exceeds {MAX_MESSAGES_PER_SESSION} messages; "
                "finalize or discard it"
            )
        return self.message_count + 1, self.message_count + 2

    def mark_analysis_pending(self, *, now: datetime | None = None) -> None:
        """Documents are linked and validated; the background LLM pass is
        about to be scheduled. No opener/context/draft yet."""
        self.assert_active()
        self.analysis_status = ChatAnalysisStatus.PENDING
        self.updated_at = now or _now()

    def open_with_documents(
        self,
        documents_context: str,
        *,
        draft: dict[str, object],
        prefilled_keys: tuple[str, ...],
        now: datetime | None = None,
    ) -> None:
        """Apply the completed background pre-analysis and record the lone
        opener message.

        Seeds ``draft`` with the values the documents already answer and
        remembers which keys those were (``documents_prefilled_keys``). The
        opener is the ONLY message at seq=1 (an assistant turn with no preceding
        user turn), so `message_count` advances by 1 — the first real
        (user, assistant) exchange still gets `next_seq_pair() == (2, 3)`.
        """
        self.assert_active()
        self.documents_context = documents_context
        self.draft = dict(draft)
        self.documents_prefilled_keys = prefilled_keys
        self.analysis_status = ChatAnalysisStatus.READY
        self.message_count += 1
        self.updated_at = now or _now()

    def mark_analysis_failed(self, *, now: datetime | None = None) -> None:
        """The background pre-analysis raised. The session stays usable
        without documents context — no opener, no prefill, no retry."""
        self.analysis_status = ChatAnalysisStatus.FAILED
        self.updated_at = now or _now()

    def record_turn(
        self,
        *,
        draft: dict[str, object],
        title: str | None,
        now: datetime | None = None,
    ) -> None:
        """Apply one completed (user, assistant) exchange to the aggregate.

        The new draft is MERGED over the existing one so values already
        collected (e.g. fields pre-filled from attached documents) are not
        dropped on a turn where the model omits them. The service pre-filters
        ``draft`` to declared, non-empty keys, so a merge never reintroduces
        stale/empty values.
        """
        self.assert_active()
        self.message_count += 2
        self.draft = {**self.draft, **draft}
        if title is not None and title.strip():
            self.draft_title = title.strip()
        self.updated_at = now or _now()

    def mark_submitted(self, *, ticket_id: UUID, now: datetime | None = None) -> None:
        self.assert_active()
        self.status = ChatSessionStatus.SUBMITTED
        self.ticket_id = ticket_id
        self.updated_at = now or _now()

    def discard(self, *, now: datetime | None = None) -> None:
        self.assert_active()
        self.status = ChatSessionStatus.DISCARDED
        self.updated_at = now or _now()

    def rename(self, title: str, *, now: datetime | None = None) -> None:
        """Override the running draft title with a human-chosen one."""
        self.assert_active()
        cleaned = title.strip()
        if not cleaned:
            raise ChatValidationError("Заголовок черновика не может быть пустым")
        self.draft_title = cleaned[:200]
        self.updated_at = now or _now()


def validate_message_content(content: str) -> str:
    content = content.strip()
    if not content:
        raise ChatValidationError("Message content cannot be empty")
    if len(content) > MESSAGE_MAX:
        raise ChatValidationError(f"Message content exceeds {MESSAGE_MAX} characters")
    return content
