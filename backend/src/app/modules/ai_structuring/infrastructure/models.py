"""SQLAlchemy ORM rows for the ai_structuring module.

Tenant-scoped (search_path), no schema= override — replayable per tenant.
The chat history is the per-session LLM context store: full message history +
consolidated draft persist in Postgres, so sessions are durable and resumable.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ChatSessionRow(Base):
    __tablename__ = "ai_chat_sessions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'submitted', 'discarded')",
            name="ai_chat_sessions_status_chk",
        ),
        CheckConstraint(
            "analysis_status IN ('none', 'pending', 'ready', 'failed')",
            name="ai_chat_sessions_analysis_status_chk",
        ),
        CheckConstraint(
            "status != 'submitted' OR ticket_id IS NOT NULL",
            name="ai_chat_sessions_submitted_ticket_chk",
        ),
        CheckConstraint("message_count >= 0", name="ai_chat_sessions_count_chk"),
        Index("ix_ai_chat_sessions_requester", "requester_id", "created_at"),
        Index("ix_ai_chat_sessions_status", "status"),
        {"info": {"tenant_scope": "tenant"}},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    requester_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    requester_sub: Mapped[str] = mapped_column(String(255), nullable=False)
    template_version_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="active")
    analysis_status: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default="none"
    )
    draft: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    draft_title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    documents_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    documents_prefilled_keys: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    message_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ChatMessageRow(Base):
    __tablename__ = "ai_chat_messages"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name="ai_chat_messages_role_chk"),
        CheckConstraint("seq >= 1", name="ai_chat_messages_seq_chk"),
        UniqueConstraint("session_id", "seq", name="ai_chat_messages_session_seq_uq"),
        {"info": {"tenant_scope": "tenant"}},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("ai_chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(10), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ChatSessionDocumentRow(Base):
    """Join table: documents attached to a session at start time.

    Owned by ai_structuring (operations are keyed by session_id and require
    the session's own access check) even though it FKs into the `documents`
    module's table — both tables live in the same tenant schema, so the FK is
    valid. Mirrors the one-way `ai_structuring -> documents` dependency used
    by SqlTemplateFieldsProvider/TicketServiceIntakeSink for sibling modules.
    """

    __tablename__ = "ai_chat_session_documents"
    __table_args__ = ({"info": {"tenant_scope": "tenant"}},)

    session_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("ai_chat_sessions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
