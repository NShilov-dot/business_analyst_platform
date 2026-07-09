"""DTOs for the analytics module.

Plain Python dataclasses — no SQLAlchemy / Pydantic / FastAPI imports.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class TicketFlowQuery:
    weeks: int


@dataclass(frozen=True, slots=True)
class FlowPoint:
    week_start: datetime.date
    created: int
    closed: int


@dataclass(frozen=True, slots=True)
class TicketFlow:
    weeks: int
    points: tuple[FlowPoint, ...]


@dataclass(frozen=True, slots=True)
class ActivityQuery:
    limit: int


@dataclass(frozen=True, slots=True)
class ActivityItem:
    entry_id: UUID
    entity_id: UUID
    ticket_title: str | None
    action: str
    status: str | None
    actor: str
    occurred_at: datetime.datetime
