"""Concrete ChatSessionRepository on AsyncSession (flush, never commit)."""

from __future__ import annotations

from typing import cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ai_structuring.domain.entities import (
    ChatMessage,
    ChatRole,
    ChatSession,
    ChatSessionStatus,
)
from app.modules.ai_structuring.domain.errors import ChatConcurrentUpdateError
from app.modules.ai_structuring.infrastructure.models import ChatMessageRow, ChatSessionRow

# Postgres SQLSTATE for a unique-constraint violation (vs FK/CHECK/NOT-NULL).
_PG_UNIQUE_VIOLATION = "23505"


def _session_to_entity(row: ChatSessionRow) -> ChatSession:
    return ChatSession(
        id=row.id,
        requester_id=row.requester_id,
        requester_sub=row.requester_sub,
        template_version_id=row.template_version_id,
        status=ChatSessionStatus(row.status),
        draft=cast(dict[str, object], row.draft),
        draft_title=row.draft_title,
        message_count=row.message_count,
        ticket_id=row.ticket_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _message_to_entity(row: ChatMessageRow) -> ChatMessage:
    return ChatMessage(
        id=row.id,
        session_id=row.session_id,
        seq=row.seq,
        role=ChatRole(row.role),
        content=row.content,
        created_at=row.created_at,
    )


class SqlAlchemyChatSessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_session(self, session: ChatSession) -> None:
        row = ChatSessionRow(
            id=session.id,
            requester_id=session.requester_id,
            requester_sub=session.requester_sub,
            template_version_id=session.template_version_id,
            status=session.status.value,
            draft=session.draft,
            draft_title=session.draft_title,
            message_count=session.message_count,
            ticket_id=session.ticket_id,
            created_at=session.created_at,
            updated_at=session.updated_at,
        )
        self._session.add(row)
        await self._session.flush()

    async def get_session_by_id(self, session_id: UUID) -> ChatSession | None:
        row = await self._session.get(ChatSessionRow, session_id)
        return _session_to_entity(row) if row is not None else None

    async def update_session(self, session: ChatSession) -> None:
        row = await self._session.get(ChatSessionRow, session.id)
        if row is None:  # pragma: no cover — service loads before updating
            return
        row.status = session.status.value
        row.draft = session.draft
        row.draft_title = session.draft_title
        row.message_count = session.message_count
        row.ticket_id = session.ticket_id
        row.updated_at = session.updated_at
        await self._session.flush()

    async def list_sessions_for_requester(
        self, requester_id: UUID, *, limit: int, offset: int
    ) -> tuple[list[ChatSession], int]:
        base = select(ChatSessionRow).where(ChatSessionRow.requester_id == requester_id)
        count_q = (
            select(func.count())
            .select_from(ChatSessionRow)
            .where(ChatSessionRow.requester_id == requester_id)
        )
        rows = (
            await self._session.scalars(
                base.order_by(ChatSessionRow.created_at.desc()).limit(limit).offset(offset)
            )
        ).all()
        total = await self._session.scalar(count_q) or 0
        return [_session_to_entity(r) for r in rows], total

    async def add_message(self, message: ChatMessage) -> None:
        row = ChatMessageRow(
            id=message.id,
            session_id=message.session_id,
            seq=message.seq,
            role=message.role.value,
            content=message.content,
            created_at=message.created_at,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            # ONLY a unique violation (sqlstate 23505) means a concurrent send
            # computed the same (session_id, seq) → retryable 409. FK / CHECK
            # violations are real bugs; re-raise rather than mask them as a race.
            if getattr(exc.orig, "sqlstate", None) != _PG_UNIQUE_VIOLATION:
                raise
            raise ChatConcurrentUpdateError(
                "Another message was recorded concurrently; please retry."
            ) from exc

    async def list_messages(self, session_id: UUID) -> list[ChatMessage]:
        rows = (
            await self._session.scalars(
                select(ChatMessageRow)
                .where(ChatMessageRow.session_id == session_id)
                .order_by(ChatMessageRow.seq)
            )
        ).all()
        return [_message_to_entity(r) for r in rows]
