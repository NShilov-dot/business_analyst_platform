"""DTOs, value objects, and page types for the departments module.

No SQLAlchemy / Pydantic / FastAPI imports allowed here — these are plain
Python dataclasses used by both the service layer and the infrastructure
adapters. The thin module pattern co-locates entities and commands here
instead of a separate domain/ directory.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import NamedTuple
from uuid import UUID

# ---------------------------------------------------------------------------
# Value objects (returned by the service and repository adapters)
# ---------------------------------------------------------------------------


@dataclass(slots=True, kw_only=True)
class Department:
    id: UUID
    name: str
    description: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True, kw_only=True)
class DepartmentMembership:
    id: UUID
    department_id: UUID
    subject: str  # Keycloak sub claim


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@dataclass(slots=True, kw_only=True)
class CreateDepartmentCommand:
    name: str
    description: str | None = None


@dataclass(slots=True, kw_only=True)
class UpdateDepartmentCommand:
    """Partial update.

    `name` and `is_active` are touched only when not None. For the nullable
    `description` field, the caller MUST set `description_set=True` to apply
    the value — that distinguishes "leave alone" from "set to null".
    """

    name: str | None = None
    description: str | None = None
    description_set: bool = False
    is_active: bool | None = None


# ---------------------------------------------------------------------------
# Queries and page
# ---------------------------------------------------------------------------


@dataclass(slots=True, kw_only=True)
class ListDepartmentsQuery:
    limit: int = 20
    offset: int = 0


class DepartmentsPage(NamedTuple):
    items: list[Department]
    total: int
    limit: int
    offset: int
