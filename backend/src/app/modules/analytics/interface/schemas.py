"""Pydantic response schemas for the analytics API.

Read-only: no request schemas needed — all queries arrive as query params.
The local `Envelope[T]` mirrors the pattern used in audit/interface/schemas.py
(avoids cross-module imports for a simple wrapper type).
"""

from __future__ import annotations

import datetime
from typing import Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.modules.analytics.application.dtos import ActivityItem, FlowPoint, TicketFlow

T = TypeVar("T")


class Envelope(BaseModel, Generic[T]):
    data: T


class FlowPointResponse(BaseModel):
    model_config = ConfigDict(from_attributes=False)

    week_start: datetime.date
    created: int
    closed: int

    @classmethod
    def from_dto(cls, dto: FlowPoint) -> FlowPointResponse:
        return cls(
            week_start=dto.week_start,
            created=dto.created,
            closed=dto.closed,
        )


class TicketFlowResponse(BaseModel):
    model_config = ConfigDict(from_attributes=False)

    weeks: int
    points: list[FlowPointResponse]

    @classmethod
    def from_dto(cls, dto: TicketFlow) -> TicketFlowResponse:
        return cls(
            weeks=dto.weeks,
            points=[FlowPointResponse.from_dto(p) for p in dto.points],
        )


class ActivityItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=False)

    entry_id: UUID
    entity_id: UUID
    ticket_title: str | None
    action: str
    status: str | None
    actor: str
    occurred_at: datetime.datetime

    @classmethod
    def from_dto(cls, dto: ActivityItem) -> ActivityItemResponse:
        return cls(
            entry_id=dto.entry_id,
            entity_id=dto.entity_id,
            ticket_title=dto.ticket_title,
            action=dto.action,
            status=dto.status,
            actor=dto.actor,
            occurred_at=dto.occurred_at,
        )
