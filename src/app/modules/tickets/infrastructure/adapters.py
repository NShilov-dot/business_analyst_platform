"""Thin adapters satisfying the tickets module's outward-facing ports.

- NullTrackerGateway    — Phase-1 TrackerPort (tracker platform undecided;
  keep tracker-specific logic behind the port, per the domain brief).
- SqlDepartmentLookup   — DepartmentLookup over the departments table.
- SqlTemplateVersionInfo — TemplateVersionInfo over the template_versions table.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.departments.infrastructure.models import DepartmentRow
from app.modules.intake_templates.domain.entities import TemplateVersionStatus
from app.modules.intake_templates.infrastructure.models import TemplateVersionRow
from app.modules.tickets.domain.entities import OpenDefectsResult, Ticket, TicketStatus
from app.modules.tickets.domain.ports import TrackerUpdate


class NullTrackerGateway:
    """No-op TrackerPort: Postgres is the only system of record in Phase 1.

    open_defects_query returns UNKNOWN (None/None) — never a fake "clean" 0.
    """

    async def mirror_create(self, ticket: Ticket) -> str | None:
        return None

    async def mirror_transition(
        self,
        *,
        ticket_id: UUID,
        external_ref: str,
        from_status: TicketStatus,
        to_status: TicketStatus,
    ) -> None:
        return None

    async def pull_updates(self, *, since: datetime) -> list[TrackerUpdate]:
        return []

    async def open_defects_query(
        self,
        *,
        ticket_id: UUID,
        external_ref: str | None,
    ) -> OpenDefectsResult:
        return OpenDefectsResult(open_p0=None, open_p1=None, checked_at=datetime.now(UTC))


class SqlDepartmentLookup:
    """DepartmentLookup seam over the tenant's departments table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def is_active_department(self, department_id: UUID) -> bool:
        is_active = await self._session.scalar(
            select(DepartmentRow.is_active).where(DepartmentRow.id == department_id)
        )
        return bool(is_active)


class SqlTemplateVersionInfo:
    """TemplateVersionInfo seam over the tenant's template_versions table.

    Closes the confirmed gap: TemplateService.validate_submission does not
    check publication status, so tickets verifies it here.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def is_published_version(self, version_id: UUID) -> bool:
        status = await self._session.scalar(
            select(TemplateVersionRow.status).where(TemplateVersionRow.id == version_id)
        )
        return status == TemplateVersionStatus.PUBLISHED.value
