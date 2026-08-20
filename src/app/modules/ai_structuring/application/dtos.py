"""Application-layer DTOs for the ai_structuring module."""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple
from uuid import UUID

from app.modules.ai_structuring.domain.entities import ChatMessage, ChatSession


@dataclass(slots=True, kw_only=True)
class StartSessionCommand:
    template_version_id: UUID
    document_ids: tuple[UUID, ...] = ()


@dataclass(slots=True, kw_only=True)
class SendMessageCommand:
    content: str


@dataclass(slots=True, kw_only=True)
class FieldState:
    """Per-field draft progress shown next to the chat."""

    key: str
    label: str
    required: bool
    value: str | None
    missing: bool  # required and not yet validly filled
    from_document: bool = False  # value seeded from an attached document


@dataclass(slots=True, kw_only=True)
class SessionDetail:
    session: ChatSession
    messages: list[ChatMessage]
    fields: list[FieldState]
    is_ready: bool  # SubmissionValidator returned no errors


@dataclass(slots=True, kw_only=True)
class TurnResult:
    """Outcome of one send_message call."""

    session: ChatSession
    reply: ChatMessage
    fields: list[FieldState]
    is_ready: bool


class SessionPage(NamedTuple):
    items: list[ChatSession]
    total: int
    limit: int
    offset: int
