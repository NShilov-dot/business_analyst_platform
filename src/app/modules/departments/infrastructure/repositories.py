"""SQLAlchemy-backed adapters for DepartmentRepository and DepartmentResolver ports.

Callers own the transaction boundary (SessionDep); these adapters `flush()`
but never `commit()`.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.departments.application.dtos import Department, DepartmentMembership
from app.modules.departments.application.errors import DepartmentNameConflictError
from app.modules.departments.infrastructure.models import (
    DepartmentMembershipRow,
    DepartmentRow,
)


def _to_dept(row: DepartmentRow) -> Department:
    return Department(
        id=row.id,
        name=row.name,
        description=row.description,
        is_active=row.is_active,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _apply_dept_to_row(dept: Department, row: DepartmentRow) -> None:
    row.name = dept.name
    row.description = dept.description
    row.is_active = dept.is_active
    row.updated_at = dept.updated_at


def _to_membership(row: DepartmentMembershipRow) -> DepartmentMembership:
    return DepartmentMembership(
        id=row.id,
        department_id=row.department_id,
        subject=row.subject,
    )


class SqlAlchemyDepartmentRepository:
    """Concrete DepartmentRepository. Session is caller-owned."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, dept: Department) -> None:
        row = DepartmentRow(
            id=dept.id,
            name=dept.name,
            description=dept.description,
            is_active=dept.is_active,
            created_at=dept.created_at,
            updated_at=dept.updated_at,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise DepartmentNameConflictError(
                f"Department named '{dept.name}' already exists in this tenant"
            ) from exc

    async def get_by_id(self, dept_id: UUID) -> Department | None:
        row = await self._session.get(DepartmentRow, dept_id)
        return _to_dept(row) if row is not None else None

    async def get_by_name(self, name: str) -> Department | None:
        row = (
            await self._session.scalars(select(DepartmentRow).where(DepartmentRow.name == name))
        ).first()
        return _to_dept(row) if row is not None else None

    async def list_departments(
        self,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[Department], int]:
        base = select(DepartmentRow)
        count_q = select(func.count()).select_from(DepartmentRow)
        rows = (
            await self._session.scalars(
                base.order_by(DepartmentRow.name).limit(limit).offset(offset)
            )
        ).all()
        total = await self._session.scalar(count_q) or 0
        return [_to_dept(r) for r in rows], total

    async def update(self, dept: Department) -> Department:
        row = await self._session.get(DepartmentRow, dept.id)
        if row is None:  # should not happen — service loads first
            raise DepartmentNameConflictError(f"Department {dept.id} not found during update")
        _apply_dept_to_row(dept, row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise DepartmentNameConflictError(
                f"Department named '{dept.name}' already exists in this tenant"
            ) from exc
        return dept

    async def add_member(self, membership: DepartmentMembership) -> None:
        row = DepartmentMembershipRow(
            id=membership.id,
            department_id=membership.department_id,
            subject=membership.subject,
        )
        self._session.add(row)
        await self._session.flush()

    async def get_membership(self, dept_id: UUID, subject: str) -> DepartmentMembership | None:
        row = (
            await self._session.scalars(
                select(DepartmentMembershipRow).where(
                    DepartmentMembershipRow.department_id == dept_id,
                    DepartmentMembershipRow.subject == subject,
                )
            )
        ).first()
        return _to_membership(row) if row is not None else None

    async def remove_member(self, membership_id: UUID) -> None:
        await self._session.execute(
            delete(DepartmentMembershipRow).where(DepartmentMembershipRow.id == membership_id)
        )
        await self._session.flush()


class SqlAlchemyDepartmentResolver:
    """Adapter for the DepartmentResolver port.

    Phase-1 seam: wiring into core/authz.py is deferred to Phase 2.
    The adapter is complete and testable; it is not yet injected anywhere.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_department_ids_for_subject(self, subject: str) -> list[UUID]:
        rows = (
            await self._session.scalars(
                select(DepartmentMembershipRow.department_id).where(
                    DepartmentMembershipRow.subject == subject
                )
            )
        ).all()
        return list(rows)
