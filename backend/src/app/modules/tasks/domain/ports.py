from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.modules.tasks.domain.entities import Task, TaskStatus


class TaskRepository(Protocol):
    """Port for task persistence. Implemented in infrastructure layer."""

    async def add(self, task: Task) -> None: ...

    async def get_by_id(self, task_id: UUID) -> Task | None: ...

    async def list_tasks(
        self,
        *,
        owner_id: UUID | None,
        status: TaskStatus | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Task], int]:
        """List tasks in the current tenant. owner_id=None → all org tasks."""
        ...

    async def update(self, task: Task) -> Task: ...

    async def delete(self, task_id: UUID) -> None: ...
