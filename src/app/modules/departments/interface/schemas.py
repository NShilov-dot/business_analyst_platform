"""Pydantic request/response schemas for the departments API.

Each Request schema has a `to_command()` method; each Response schema has a
`from_entity()` classmethod. `Envelope[T]` and `PagedEnvelope[T]` follow the
same pattern as `tasks/interface/schemas.py`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.departments.application.dtos import (
    CreateDepartmentCommand,
    Department,
    DepartmentMembership,
    DepartmentsPage,
    UpdateDepartmentCommand,
)

T = TypeVar("T")

NAME_MAX = 200
DESCRIPTION_MAX = 2_000


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class CreateDepartmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=NAME_MAX)
    description: str | None = Field(default=None, max_length=DESCRIPTION_MAX)

    @field_validator("name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("name must not be blank")
        return stripped

    def to_command(self) -> CreateDepartmentCommand:
        return CreateDepartmentCommand(name=self.name, description=self.description)


class UpdateDepartmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=NAME_MAX)
    description: str | None = Field(default=None, max_length=DESCRIPTION_MAX)
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def strip_name(cls, v: str | None) -> str | None:
        if v is None:
            return v
        stripped = v.strip()
        if not stripped:
            raise ValueError("name must not be blank")
        return stripped

    def to_command(self) -> UpdateDepartmentCommand:
        provided = self.model_fields_set
        return UpdateDepartmentCommand(
            name=self.name,
            description=self.description,
            description_set="description" in provided,
            is_active=self.is_active,
        )


class AddMemberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str = Field(
        min_length=1,
        max_length=255,
        description="Keycloak subject (sub claim) to add as a department member",
    )


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class DepartmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_entity(cls, dept: Department) -> DepartmentResponse:
        return cls.model_validate(dept)


class MembershipResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    department_id: UUID
    subject: str

    @classmethod
    def from_entity(cls, m: DepartmentMembership) -> MembershipResponse:
        return cls.model_validate(m)


# ---------------------------------------------------------------------------
# Envelope wrappers
# ---------------------------------------------------------------------------


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
    def from_page(cls, page: DepartmentsPage) -> PagedEnvelope[DepartmentResponse]:
        return PagedEnvelope[DepartmentResponse](
            data=[DepartmentResponse.from_entity(d) for d in page.items],
            meta=PageMeta(total=page.total, limit=page.limit, offset=page.offset),
        )
