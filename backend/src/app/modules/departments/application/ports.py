"""Port contracts for the departments module.

`DepartmentRepository` is the persistence port consumed by the service.
`DepartmentResolver` is the Phase-1 seam declared here but NOT wired into
`core/` yet — Phase 2 DataZonePolicy filtering will consume it via
`core/authz.py`.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.modules.departments.application.dtos import Department, DepartmentMembership


class DepartmentRepository(Protocol):
    """Port for department + membership persistence."""

    async def add(self, dept: Department) -> None: ...

    async def get_by_id(self, dept_id: UUID) -> Department | None: ...

    async def get_by_name(self, name: str) -> Department | None: ...

    async def list_departments(
        self,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[Department], int]: ...

    async def update(self, dept: Department) -> Department: ...

    async def add_member(self, membership: DepartmentMembership) -> None: ...

    async def get_membership(self, dept_id: UUID, subject: str) -> DepartmentMembership | None: ...

    async def remove_member(self, membership_id: UUID) -> None: ...


class DepartmentResolver(Protocol):
    """Phase-1 seam: resolve the department IDs that a Keycloak subject belongs to.

    This port is the anchor for the DataZonePolicy filtering planned in Phase 2.
    A repository-backed adapter (`SqlAlchemyDepartmentResolver`) exists in the
    infrastructure layer, but the wiring into `core/authz.py` is intentionally
    deferred — Phase 1 guarantees only the seam, not the filter.
    """

    async def get_department_ids_for_subject(self, subject: str) -> list[UUID]: ...
