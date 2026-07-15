"""FastAPI router for /v1/audit.

Access gate: `tenant_admin` OR `platform_admin` (ANY-of intersection —
see departments/interface/router.py for the pattern).

Read-only: no write endpoints are exposed.  All writes go through the
AuditEventSubscriber triggered by the EventBus.

Rate limiting and CSRF checks mirror the departments router.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.deps import PrincipalDep, SessionDep, check_csrf, check_rate_limit
from app.core.errors import PermissionDeniedError
from app.core.security import Principal
from app.modules.audit.application.dtos import ListAuditEntriesQuery
from app.modules.audit.application.services import AuditService
from app.modules.audit.infrastructure.repositories import SqlAlchemyAuditRepository
from app.modules.audit.interface.schemas import AuditEntryResponse, PagedEnvelope

router = APIRouter(
    prefix="/audit",
    tags=["audit"],
    dependencies=[Depends(check_rate_limit), Depends(check_csrf)],
)

# ANY-of: either of these roles may read the audit log.
_ALLOWED_ROLES = frozenset({"tenant_admin", "platform_admin"})


async def _require_audit_reader(principal: PrincipalDep) -> Principal:
    """Raise 403 unless the principal holds tenant_admin or platform_admin."""
    if not principal.roles & _ALLOWED_ROLES:
        raise PermissionDeniedError(f"Requires one of: {sorted(_ALLOWED_ROLES)}")
    return principal


AuditReaderDep = Annotated[Principal, Depends(_require_audit_reader)]


async def _service(session: SessionDep) -> AuditService:
    return AuditService(repo=SqlAlchemyAuditRepository(session))


ServiceDep = Annotated[AuditService, Depends(_service)]


# ---------------------------------------------------------------------------
# GET /v1/audit — paged list with optional filters
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=PagedEnvelope[AuditEntryResponse],
    summary="List audit log entries (tenant_admin / platform_admin)",
)
async def list_audit_entries(
    _principal: AuditReaderDep,
    service: ServiceDep,
    entity_type: Annotated[
        str | None,
        Query(
            description="Filter by entity type (e.g. 'task', 'department')",
            min_length=1,
            max_length=100,
        ),
    ] = None,
    entity_id: Annotated[
        UUID | None,
        Query(description="Filter by entity UUID"),
    ] = None,
    action: Annotated[
        str | None,
        Query(
            description="Filter by action (e.g. 'created', 'updated')",
            min_length=1,
            max_length=100,
        ),
    ] = None,
    actor: Annotated[
        str | None,
        Query(
            description="Filter by actor (Keycloak sub)",
            min_length=1,
            max_length=255,
        ),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PagedEnvelope[AuditEntryResponse]:
    page = await service.list(
        query=ListAuditEntriesQuery(
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            actor=actor,
            limit=limit,
            offset=offset,
        )
    )
    return PagedEnvelope.from_page(page)
