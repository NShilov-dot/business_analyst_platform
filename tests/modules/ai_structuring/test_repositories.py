"""Unit tests for SqlAlchemyChatSessionRepository error translation (no DB needed)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.modules.ai_structuring.domain.entities import ChatMessage, ChatRole
from app.modules.ai_structuring.domain.errors import ChatConcurrentUpdateError
from app.modules.ai_structuring.infrastructure.repositories import (
    SqlAlchemyChatSessionRepository,
)


class _Orig(Exception):
    """Stand-in for the asyncpg DBAPI error, carrying a Postgres sqlstate."""

    def __init__(self, sqlstate: str) -> None:
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


class _FlushSession:
    """Fake AsyncSession whose flush() raises an IntegrityError with a given sqlstate."""

    def __init__(self, sqlstate: str) -> None:
        self._sqlstate = sqlstate

    def add(self, _row: Any) -> None:
        pass

    async def flush(self) -> None:
        raise IntegrityError("INSERT ...", {}, _Orig(self._sqlstate))


def _message(seq: int = 1) -> ChatMessage:
    return ChatMessage(
        id=uuid4(),
        session_id=uuid4(),
        seq=seq,
        role=ChatRole.USER,
        content="hi",
        created_at=datetime(2026, 7, 9, tzinfo=UTC),
    )


async def test_add_message_maps_unique_violation_to_concurrent_update() -> None:
    repo = SqlAlchemyChatSessionRepository(_FlushSession("23505"))  # type: ignore[arg-type]
    with pytest.raises(ChatConcurrentUpdateError):
        await repo.add_message(_message())


async def test_add_message_reraises_non_unique_integrity_error() -> None:
    # An FK violation (23503) is a real bug, not a race — must not be masked.
    repo = SqlAlchemyChatSessionRepository(_FlushSession("23503"))  # type: ignore[arg-type]
    with pytest.raises(IntegrityError):
        await repo.add_message(_message())
