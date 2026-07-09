"""Service-level tests with an in-memory fake repository.

Visibility model: reads are org-wide (any member sees every task in the org);
writes (update/transition/delete) are restricted to the owner OR an admin
(`can_manage_any`).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.modules.tasks.application.dtos import (
    CreateTaskCommand,
    ListTasksQuery,
    UpdateTaskCommand,
)
from app.modules.tasks.application.services import TaskService
from app.modules.tasks.domain.entities import Task, TaskPriority, TaskStatus
from app.modules.tasks.domain.errors import TaskAccessDeniedError, TaskNotFoundError


class FakeRepo:
    def __init__(self) -> None:
        self._by_id: dict[UUID, Task] = {}

    async def add(self, task: Task) -> None:
        self._by_id[task.id] = task

    async def get_by_id(self, task_id: UUID) -> Task | None:
        return self._by_id.get(task_id)

    async def list_tasks(
        self,
        *,
        owner_id: UUID | None,
        status: TaskStatus | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Task], int]:
        rows = list(self._by_id.values())
        if owner_id is not None:
            rows = [t for t in rows if t.owner_id == owner_id]
        if status is not None:
            rows = [t for t in rows if t.status == status]
        rows.sort(key=lambda t: t.created_at, reverse=True)
        return rows[offset : offset + limit], len(rows)

    async def update(self, task: Task) -> Task:
        self._by_id[task.id] = task
        return task

    async def delete(self, task_id: UUID) -> None:
        self._by_id.pop(task_id, None)


@pytest.fixture
def repo() -> FakeRepo:
    return FakeRepo()


@pytest.fixture
def service(repo: FakeRepo) -> TaskService:
    return TaskService(repo=repo, clock=lambda: datetime(2026, 6, 16, 12, 0, tzinfo=UTC))


@pytest.mark.asyncio
async def test_create_persists(service: TaskService, repo: FakeRepo) -> None:
    owner = uuid4()
    task = await service.create(owner_id=owner, command=CreateTaskCommand(title="ship"))
    assert await repo.get_by_id(task.id) is not None
    assert task.owner_id == owner


# ---------------------------------------------------------------------------
# Reads are org-wide
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_is_org_wide(service: TaskService) -> None:
    alice, bob = uuid4(), uuid4()
    task = await service.create(owner_id=alice, command=CreateTaskCommand(title="x"))
    # Bob (a different member of the same org) can READ Alice's task.
    got = await service.get(task_id=task.id)
    assert got.id == task.id and got.owner_id == alice
    _ = bob  # both are org members; ownership doesn't gate reads


@pytest.mark.asyncio
async def test_get_missing_is_404(service: TaskService) -> None:
    with pytest.raises(TaskNotFoundError):
        await service.get(task_id=uuid4())


@pytest.mark.asyncio
async def test_list_is_org_wide_by_default_and_filterable_to_mine(service: TaskService) -> None:
    alice, bob = uuid4(), uuid4()
    await service.create(owner_id=alice, command=CreateTaskCommand(title="a"))
    await service.create(owner_id=bob, command=CreateTaskCommand(title="b"))

    everyone = await service.list(query=ListTasksQuery())
    assert everyone.total == 2  # all org tasks

    mine = await service.list(query=ListTasksQuery(), owner_id=alice)
    assert mine.total == 1 and mine.items[0].owner_id == alice


@pytest.mark.asyncio
async def test_list_status_filter(service: TaskService) -> None:
    alice = uuid4()
    t1 = await service.create(owner_id=alice, command=CreateTaskCommand(title="a"))
    await service.create(owner_id=alice, command=CreateTaskCommand(title="b"))
    await service.complete(task_id=t1.id, actor_id=alice, can_manage_any=False)

    page = await service.list(query=ListTasksQuery(status=TaskStatus.OPEN))
    assert page.total == 1 and page.items[0].status is TaskStatus.OPEN


# ---------------------------------------------------------------------------
# Writes: owner or admin only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_owner_can_update(service: TaskService) -> None:
    alice = uuid4()
    task = await service.create(
        owner_id=alice, command=CreateTaskCommand(title="x", description="initial")
    )
    updated = await service.update(
        task_id=task.id,
        actor_id=alice,
        can_manage_any=False,
        command=UpdateTaskCommand(description=None, description_set=True),
    )
    assert updated.description is None


@pytest.mark.asyncio
async def test_update_preserves_description_when_not_set(service: TaskService) -> None:
    alice = uuid4()
    task = await service.create(
        owner_id=alice, command=CreateTaskCommand(title="x", description="keep")
    )
    updated = await service.update(
        task_id=task.id,
        actor_id=alice,
        can_manage_any=False,
        command=UpdateTaskCommand(priority=TaskPriority.HIGH),
    )
    assert updated.description == "keep" and updated.priority is TaskPriority.HIGH


@pytest.mark.asyncio
async def test_non_owner_cannot_modify_or_delete(service: TaskService, repo: FakeRepo) -> None:
    alice, bob = uuid4(), uuid4()
    task = await service.create(owner_id=alice, command=CreateTaskCommand(title="x"))
    with pytest.raises(TaskAccessDeniedError):
        await service.update(
            task_id=task.id,
            actor_id=bob,
            can_manage_any=False,
            command=UpdateTaskCommand(title="hijack"),
        )
    with pytest.raises(TaskAccessDeniedError):
        await service.delete(task_id=task.id, actor_id=bob, can_manage_any=False)
    assert await repo.get_by_id(task.id) is not None  # untouched


@pytest.mark.asyncio
async def test_admin_can_manage_any_task(service: TaskService, repo: FakeRepo) -> None:
    alice, admin = uuid4(), uuid4()
    task = await service.create(owner_id=alice, command=CreateTaskCommand(title="x"))
    # tenant_admin (can_manage_any=True) may complete and delete another's task.
    await service.complete(task_id=task.id, actor_id=admin, can_manage_any=True)
    await service.delete(task_id=task.id, actor_id=admin, can_manage_any=True)
    assert await repo.get_by_id(task.id) is None


@pytest.mark.asyncio
async def test_write_on_missing_task_is_404(service: TaskService) -> None:
    with pytest.raises(TaskNotFoundError):
        await service.delete(task_id=uuid4(), actor_id=uuid4(), can_manage_any=True)
