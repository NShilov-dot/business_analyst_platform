"""Pydantic schemas for the notifications API.

The local `Envelope[T]` mirrors the pattern used in analytics/audit schemas
(avoids cross-module imports for a simple wrapper type).
"""

from __future__ import annotations

import datetime
from typing import Annotated, Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.notifications.application.dtos import (
    NotificationFeed,
    NotificationItem,
)

T = TypeVar("T")


class Envelope(BaseModel, Generic[T]):
    data: T


class NotificationItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=False)

    id: UUID
    ticket_id: UUID
    ticket_title: str | None
    action: str
    actor: str
    occurred_at: datetime.datetime
    is_read: bool

    @classmethod
    def from_dto(cls, dto: NotificationItem) -> NotificationItemResponse:
        return cls(
            id=dto.id,
            ticket_id=dto.ticket_id,
            ticket_title=dto.ticket_title,
            action=dto.action,
            actor=dto.actor,
            occurred_at=dto.occurred_at,
            is_read=dto.is_read,
        )


class NotificationFeedResponse(BaseModel):
    model_config = ConfigDict(from_attributes=False)

    items: list[NotificationItemResponse]
    unread_count: int

    @classmethod
    def from_dto(cls, dto: NotificationFeed) -> NotificationFeedResponse:
        return cls(
            items=[NotificationItemResponse.from_dto(i) for i in dto.items],
            unread_count=dto.unread_count,
        )


class MarkReadRequest(BaseModel):
    """The audit entry ids to dismiss for the calling user.

    Capped because the write is not checked against participation — an
    unbounded list would let a caller bloat their own read table.
    """

    entry_ids: Annotated[list[UUID], Field(min_length=1, max_length=100)]


class MarkReadResponse(BaseModel):
    marked: int
