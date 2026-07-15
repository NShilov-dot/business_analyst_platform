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


class ChatSessionStatus(StrEnum):
    ACTIVE = "active"
    SUBMITTED = "submitted"  # ticket created & sent to triage by the human
    DISCARDED = "discarded"


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


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True, kw_only=True)
class ChatSession:
    """Aggregate root for one AI-assisted intake conversation.

    ``draft`` is the consolidated intake payload accumulated over the chat;
    it is replaced (not merged) on every assistant turn so the LLM stays the
    single writer of its own draft, filtered to declared template keys by the
    application service.
    """

    id: UUID
    requester_id: UUID
    requester_sub: str
    template_version_id: UUID
    status: ChatSessionStatus
    draft: dict[str, object]
    draft_title: str | None
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
            draft={},
            draft_title=None,
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

    def next_seq_pair(self) -> tuple[int, int]:
        """Sequence numbers for the next (user, assistant) message pair."""
        if self.message_count + 2 > MAX_MESSAGES_PER_SESSION:
            raise ChatValidationError(
                f"Chat session exceeds {MAX_MESSAGES_PER_SESSION} messages; "
                "finalize or discard it"
            )
        return self.message_count + 1, self.message_count + 2

    def record_turn(
        self,
        *,
        draft: dict[str, object],
        title: str | None,
        now: datetime | None = None,
    ) -> None:
        """Apply one completed (user, assistant) exchange to the aggregate."""
        self.assert_active()
        self.message_count += 2
        self.draft = draft
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


def validate_message_content(content: str) -> str:
    content = content.strip()
    if not content:
        raise ChatValidationError("Message content cannot be empty")
    if len(content) > MESSAGE_MAX:
        raise ChatValidationError(f"Message content exceeds {MESSAGE_MAX} characters")
    return content
