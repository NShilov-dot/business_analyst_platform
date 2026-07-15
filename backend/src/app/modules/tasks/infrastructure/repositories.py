from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.tasks.domain.entities import Task, TaskPriority, TaskStatus
from app.modules.tasks.domain.errors import TaskNotFoundError
from app.modules.tasks.infrastructure.models import TaskRow


def _to_entity(row: TaskRow) -> Task:
    return Task(
        id=row.id,
        owner_id=row.owner_id,
        title=row.title,
        description=row.description,
        status=TaskStatus(row.status),
        priority=TaskPriority(row.priority),
        due_at=row.due_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        completed_at=row.completed_at,
    )


def _apply_to_row(task: Task, row: TaskRow) -> None:
    row.title = task.title
    row.description = task.description
    row.status = task.status.value
    row.priority = task.priority.value
    row.due_at = task.due_at
    row.updated_at = task.updated_at
    row.completed_at = task.completed_at


class SqlAlchemyTaskRepository:
    """Concrete TaskRepository on AsyncSession. Caller owns the transaction —
    the session-per-request dependency commits/rolls back."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, task: Task) -> None:
        row = TaskRow(
            id=task.id,
            owner_id=task.owner_id,
            title=task.title,
            description=task.description,
            status=task.status.value,
            priority=task.priority.value,
            due_at=task.due_at,
            created_at=task.created_at,
            updated_at=task.updated_at,
            completed_at=task.completed_at,
        )
        self._session.add(row)
        await self._session.flush()

    async def get_by_id(self, task_id: UUID) -> Task | None:
        row = await self._session.get(TaskRow, task_id)
        return _to_entity(row) if row is not None else None

    async def list_tasks(
        self,
        *,
        owner_id: UUID | None,
        status: TaskStatus | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Task], int]:
        # Tenant isolation is enforced by the per-request schema search_path, so
        # "all tasks" here means all tasks of the current organization. owner_id
        # narrows to one member's tasks (the "my tasks" view).
        base = select(TaskRow)
        count_q = select(func.count()).select_from(TaskRow)
        if owner_id is not None:
            base = base.where(TaskRow.owner_id == owner_id)
            count_q = count_q.where(TaskRow.owner_id == owner_id)
        if status is not None:
            base = base.where(TaskRow.status == status.value)
            count_q = count_q.where(TaskRow.status == status.value)

        rows = (
            await self._session.scalars(
                base.order_by(TaskRow.created_at.desc()).limit(limit).offset(offset)
            )
        ).all()
        total = await self._session.scalar(count_q) or 0
        return [_to_entity(r) for r in rows], total

    async def update(self, task: Task) -> Task:
        row = await self._session.get(TaskRow, task.id)
        if row is None:
            raise TaskNotFoundError(f"Task {task.id} not found")
        _apply_to_row(task, row)
        await self._session.flush()
        return task

    async def delete(self, task_id: UUID) -> None:
        await self._session.execute(delete(TaskRow).where(TaskRow.id == task_id))
        await self._session.flush()
