"""Department application service.

Orchestrates domain logic, enforces invariants, and delegates persistence to
the DepartmentRepository port. The injected `clock` makes time deterministic
in tests without monkeypatching.

Rules enforced here:
- Department names are unique per tenant (search_path provides tenant scope).
- Membership add/remove is idempotent: duplicate add returns the existing
  membership; remove of a non-member is a no-op (after verifying the dept
  exists).
- Every mutating operation emits a domain event via the injected publisher so
  the audit log is written atomically in the same transaction.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.core.events import EventPublisher
from app.modules.departments.application.dtos import (
    CreateDepartmentCommand,
    Department,
    DepartmentMembership,
    DepartmentsPage,
    ListDepartmentsQuery,
    UpdateDepartmentCommand,
)
from app.modules.departments.application.errors import (
    DepartmentNameConflictError,
    DepartmentNotFoundError,
)
from app.modules.departments.application.ports import DepartmentRepository


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class DepartmentService:
    """All members of a tenant see all departments; only admins may mutate them.

    The router layer enforces the admin gate via `_ADMIN_ROLES`; this service
    does not re-check roles — it trusts the caller to have done so.

    `publisher` is required.  Pass `NoopPublisher()` from `core.events` only
    when you explicitly want to suppress auditing (e.g. seeding scripts).
    """

    repo: DepartmentRepository
    publisher: EventPublisher
    clock: Callable[[], datetime] = field(default=_utc_now)

    async def create(self, *, command: CreateDepartmentCommand) -> Department:
        name = command.name.strip()
        existing = await self.repo.get_by_name(name)
        if existing is not None:
            raise DepartmentNameConflictError(
                f"Department named '{name}' already exists in this tenant"
            )
        ts = self.clock()
        dept = Department(
            id=uuid4(),
            name=name,
            description=command.description,
            is_active=True,
            created_at=ts,
            updated_at=ts,
        )
        await self.repo.add(dept)
        await self.publisher(
            "department",
            dept.id,
            "created",
            after={"name": dept.name, "description": dept.description, "is_active": dept.is_active},
        )
        return dept

    async def get(self, *, dept_id: UUID) -> Department:
        dept = await self.repo.get_by_id(dept_id)
        if dept is None:
            raise DepartmentNotFoundError(f"Department {dept_id} not found")
        return dept

    async def list(self, *, query: ListDepartmentsQuery) -> DepartmentsPage:
        items, total = await self.repo.list_departments(limit=query.limit, offset=query.offset)
        return DepartmentsPage(items=items, total=total, limit=query.limit, offset=query.offset)

    async def update(self, *, dept_id: UUID, command: UpdateDepartmentCommand) -> Department:
        dept = await self.repo.get_by_id(dept_id)
        if dept is None:
            raise DepartmentNotFoundError(f"Department {dept_id} not found")

        before: dict[str, object] = {}
        after: dict[str, object] = {}

        if command.name is not None:
            new_name = command.name.strip()
            if new_name != dept.name:
                conflict = await self.repo.get_by_name(new_name)
                if conflict is not None:
                    raise DepartmentNameConflictError(
                        f"Department named '{new_name}' already exists in this tenant"
                    )
                before["name"] = dept.name
                after["name"] = new_name
            dept.name = new_name

        if command.description_set:
            if dept.description != command.description:
                before["description"] = dept.description
                after["description"] = command.description
            dept.description = command.description

        if command.is_active is not None:
            if dept.is_active != command.is_active:
                before["is_active"] = dept.is_active
                after["is_active"] = command.is_active
            dept.is_active = command.is_active

        if after:
            dept.updated_at = self.clock()
            await self.repo.update(dept)
            await self.publisher(
                "department",
                dept.id,
                "updated",
                before=before,
                after=after,
            )
        return dept

    async def add_member(self, *, dept_id: UUID, subject: str) -> DepartmentMembership:
        """Add a Keycloak subject to a department.

        Idempotent: if the subject is already a member, the existing membership
        is returned without error and NO event is emitted.
        """
        dept = await self.repo.get_by_id(dept_id)
        if dept is None:
            raise DepartmentNotFoundError(f"Department {dept_id} not found")

        existing = await self.repo.get_membership(dept_id, subject)
        if existing is not None:
            return existing  # idempotent — do NOT emit

        membership = DepartmentMembership(
            id=uuid4(),
            department_id=dept_id,
            subject=subject,
        )
        await self.repo.add_member(membership)
        await self.publisher(
            "department",
            dept_id,
            "membership.added",
            after={"subject": subject},
        )
        return membership

    async def remove_member(self, *, dept_id: UUID, subject: str) -> None:
        """Remove a Keycloak subject from a department.

        Idempotent: if the subject is not a member, this is a no-op (the
        department must still exist — a missing department raises 404) and NO
        event is emitted.
        """
        dept = await self.repo.get_by_id(dept_id)
        if dept is None:
            raise DepartmentNotFoundError(f"Department {dept_id} not found")

        existing = await self.repo.get_membership(dept_id, subject)
        if existing is None:
            return  # already not a member — idempotent no-op, do NOT emit

        await self.repo.remove_member(existing.id)
        await self.publisher(
            "department",
            dept_id,
            "membership.removed",
            before={"subject": subject},
        )
