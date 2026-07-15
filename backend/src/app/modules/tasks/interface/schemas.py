from __future__ import annotations

from datetime import datetime
from typing import Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.tasks.application.dtos import (
    CreateTaskCommand,
    Page,
    UpdateTaskCommand,
)
from app.modules.tasks.domain.entities import Task, TaskPriority, TaskStatus

T = TypeVar("T")


class CreateTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4_000)
    priority: TaskPriority = TaskPriority.MEDIUM
    due_at: datetime | None = None

    def to_command(self) -> CreateTaskCommand:
        return CreateTaskCommand(
            title=self.title,
            description=self.description,
            priority=self.priority,
            due_at=self.due_at,
        )


class UpdateTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4_000)
    priority: TaskPriority | None = None
    due_at: datetime | None = None

    def to_command(self) -> UpdateTaskCommand:
        provided = self.model_fields_set
        return UpdateTaskCommand(
            title=self.title,
            description=self.description,
            description_set="description" in provided,
            priority=self.priority,
            due_at=self.due_at,
            due_at_set="due_at" in provided,
        )


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    owner_id: UUID
    title: str
    description: str | None
    status: TaskStatus
    priority: TaskPriority
    due_at: datetime | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None

    @classmethod
    def from_entity(cls, task: Task) -> TaskResponse:
        return cls.model_validate(task)


class PageMeta(BaseModel):
    total: int
    limit: int
    offset: int


class Envelope(BaseModel, Generic[T]):
    data: T


class PagedEnvelope(BaseModel, Generic[T]):
    data: list[T]
    meta: PageMeta

    @classmethod
    def from_page(cls, page: Page) -> PagedEnvelope[TaskResponse]:
        return PagedEnvelope[TaskResponse](
            data=[TaskResponse.from_entity(t) for t in page.items],
            meta=PageMeta(total=page.total, limit=page.limit, offset=page.offset),
        )
