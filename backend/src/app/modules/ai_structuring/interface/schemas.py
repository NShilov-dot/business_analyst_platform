"""Pydantic schemas for /v1/intake-chat."""

from __future__ import annotations

from datetime import datetime
from typing import Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.ai_structuring.application.dtos import (
    FieldState,
    SendMessageCommand,
    SessionDetail,
    SessionPage,
    StartSessionCommand,
    TurnResult,
)
from app.modules.ai_structuring.domain.entities import (
    MESSAGE_MAX,
    ChatAnalysisStatus,
    ChatMessage,
    ChatRole,
    ChatSession,
    ChatSessionStatus,
)
from app.modules.intake_templates.domain.entities import FREE_FORM_VERSION_ID

T = TypeVar("T")

# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------


_MAX_SESSION_DOCUMENTS = 20


class StartSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_version_id: UUID = FREE_FORM_VERSION_ID
    document_ids: list[UUID] = Field(default_factory=list, max_length=_MAX_SESSION_DOCUMENTS)

    def to_command(self) -> StartSessionCommand:
        return StartSessionCommand(
            template_version_id=self.template_version_id,
            document_ids=tuple(self.document_ids),
        )


class SendMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=MESSAGE_MAX)

    def to_command(self) -> SendMessageCommand:
        return SendMessageCommand(content=self.content)


class RenameSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft_title: str = Field(min_length=1, max_length=200)


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


class ChatSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    template_version_id: UUID
    status: ChatSessionStatus
    analysis_status: ChatAnalysisStatus
    draft: dict[str, object]
    draft_title: str | None
    message_count: int
    ticket_id: UUID | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_entity(cls, session: ChatSession) -> ChatSessionResponse:
        return cls.model_validate(session)


class ChatMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    seq: int
    role: ChatRole
    content: str
    created_at: datetime

    @classmethod
    def from_entity(cls, message: ChatMessage) -> ChatMessageResponse:
        return cls.model_validate(message)


class FieldStateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    label: str
    required: bool
    value: str | None
    missing: bool
    from_document: bool

    @classmethod
    def from_state(cls, state: FieldState) -> FieldStateResponse:
        return cls.model_validate(state)


class SessionDetailResponse(BaseModel):
    session: ChatSessionResponse
    messages: list[ChatMessageResponse]
    fields: list[FieldStateResponse]
    is_ready: bool

    @classmethod
    def from_detail(cls, detail: SessionDetail) -> SessionDetailResponse:
        return cls(
            session=ChatSessionResponse.from_entity(detail.session),
            messages=[ChatMessageResponse.from_entity(m) for m in detail.messages],
            fields=[FieldStateResponse.from_state(f) for f in detail.fields],
            is_ready=detail.is_ready,
        )


class TranscriptionResponse(BaseModel):
    text: str


class WarmupResponse(BaseModel):
    status: str


class TurnResponse(BaseModel):
    session: ChatSessionResponse
    reply: ChatMessageResponse
    fields: list[FieldStateResponse]
    is_ready: bool

    @classmethod
    def from_turn(cls, turn: TurnResult) -> TurnResponse:
        return cls(
            session=ChatSessionResponse.from_entity(turn.session),
            reply=ChatMessageResponse.from_entity(turn.reply),
            fields=[FieldStateResponse.from_state(f) for f in turn.fields],
            is_ready=turn.is_ready,
        )


# ---------------------------------------------------------------------------
# Envelopes
# ---------------------------------------------------------------------------


class PageMeta(BaseModel):
    total: int
    limit: int
    offset: int


class Envelope(BaseModel, Generic[T]):
    data: T


class PagedEnvelope(BaseModel, Generic[T]):
    data: list[T]
    meta: PageMeta

    @classmethod
    def from_page(cls, page: SessionPage) -> PagedEnvelope[ChatSessionResponse]:
        return PagedEnvelope[ChatSessionResponse](
            data=[ChatSessionResponse.from_entity(s) for s in page.items],
            meta=PageMeta(total=page.total, limit=page.limit, offset=page.offset),
        )
