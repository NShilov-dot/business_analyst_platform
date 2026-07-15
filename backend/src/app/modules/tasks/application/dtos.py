from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import NamedTuple

from app.modules.tasks.domain.entities import Task, TaskPriority, TaskStatus


@dataclass(slots=True, kw_only=True)
class CreateTaskCommand:
    title: str
    description: str | None = None
    priority: TaskPriority = TaskPriority.MEDIUM
    due_at: datetime | None = None


@dataclass(slots=True, kw_only=True)
class UpdateTaskCommand:
    """Partial update. For nullable fields, the `*_set` flag MUST be true to
    apply the value — that lets the caller distinguish "leave alone" from
    "set to null". The interface layer translates `fields_set` from the
    Pydantic request body into these flags.
    """

    title: str | None = None
    description: str | None = None
    description_set: bool = False
    priority: TaskPriority | None = None
    due_at: datetime | None = None
    due_at_set: bool = False


@dataclass(slots=True, kw_only=True)
class ListTasksQuery:
    status: TaskStatus | None = None
    limit: int = 20
    offset: int = 0


class Page(NamedTuple):
    items: list[Task]
    total: int
    limit: int
    offset: int
