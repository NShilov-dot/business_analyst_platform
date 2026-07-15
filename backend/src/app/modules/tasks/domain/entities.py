"""Task domain entity. No SQLAlchemy / Pydantic / FastAPI imports allowed in this module."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from app.modules.tasks.domain.errors import (
    TaskTransitionError,
    TaskValidationError,
)


class TaskStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CANCELLED = "cancelled"


class TaskPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


TITLE_MAX = 200
DESCRIPTION_MAX = 4_000


def _now() -> datetime:
    return datetime.now(UTC)


def _validate_title(title: str) -> str:
    title = title.strip()
    if not title:
        raise TaskValidationError("Task title cannot be empty")
    if len(title) > TITLE_MAX:
        raise TaskValidationError(f"Task title exceeds {TITLE_MAX} characters")
    return title


def _validate_description(description: str | None) -> str | None:
    if description is None:
        return None
    if len(description) > DESCRIPTION_MAX:
        raise TaskValidationError(f"Task description exceeds {DESCRIPTION_MAX} characters")
    return description


def _validate_due_at(due_at: datetime | None) -> datetime | None:
    if due_at is None:
        return None
    if due_at.tzinfo is None:
        raise TaskValidationError("due_at must be timezone-aware")
    return due_at


@dataclass(slots=True, kw_only=True)
class Task:
    """Aggregate root. Mutating methods enforce business invariants."""

    id: UUID
    owner_id: UUID
    title: str
    description: str | None
    status: TaskStatus
    priority: TaskPriority
    due_at: datetime | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = field(default=None)

    @classmethod
    def create(
        cls,
        *,
        owner_id: UUID,
        title: str,
        description: str | None = None,
        priority: TaskPriority = TaskPriority.MEDIUM,
        due_at: datetime | None = None,
        now: datetime | None = None,
    ) -> Task:
        ts = now or _now()
        return cls(
            id=uuid4(),
            owner_id=owner_id,
            title=_validate_title(title),
            description=_validate_description(description),
            status=TaskStatus.OPEN,
            priority=priority,
            due_at=_validate_due_at(due_at),
            created_at=ts,
            updated_at=ts,
            completed_at=None,
        )

    def update_details(
        self,
        *,
        title: str | None = None,
        description: str | None = None,
        set_description: bool = False,
        priority: TaskPriority | None = None,
        due_at: datetime | None = None,
        set_due_at: bool = False,
        now: datetime | None = None,
    ) -> None:
        """Partial update.

        `title` / `priority` are touched only when not None. For nullable fields
        (`description`, `due_at`), the caller MUST set the matching `set_*` flag
        to apply the value — that distinguishes "leave alone" from "set to null".
        """
        if self.status in {TaskStatus.DONE, TaskStatus.CANCELLED}:
            raise TaskTransitionError(
                f"Cannot edit a task in status {self.status!s}",
            )
        if title is not None:
            self.title = _validate_title(title)
        if set_description:
            self.description = _validate_description(description)
        if priority is not None:
            self.priority = priority
        if set_due_at:
            self.due_at = _validate_due_at(due_at)
        self.updated_at = now or _now()

    def start(self, *, now: datetime | None = None) -> None:
        if self.status != TaskStatus.OPEN:
            raise TaskTransitionError(
                f"Only OPEN tasks can be started (was {self.status!s})",
            )
        self.status = TaskStatus.IN_PROGRESS
        self.updated_at = now or _now()

    def complete(self, *, now: datetime | None = None) -> None:
        if self.status in {TaskStatus.DONE, TaskStatus.CANCELLED}:
            raise TaskTransitionError(
                f"Cannot complete a task in status {self.status!s}",
            )
        ts = now or _now()
        self.status = TaskStatus.DONE
        self.completed_at = ts
        self.updated_at = ts

    def cancel(self, *, now: datetime | None = None) -> None:
        if self.status in {TaskStatus.DONE, TaskStatus.CANCELLED}:
            raise TaskTransitionError(
                f"Cannot cancel a task in status {self.status!s}",
            )
        self.status = TaskStatus.CANCELLED
        self.updated_at = now or _now()
