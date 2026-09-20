"""FastAPI router for /api/v1/departments.

Read endpoints (GET list, GET by id) are open to any authenticated tenant
member. Write endpoints (POST create, PATCH update, POST members, DELETE
members) require `tenant_admin` OR `platform_admin`.

Rate limiting and CSRF checks mirror the tasks router.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Response, status

from app.core.deps import PrincipalDep, SessionDep, check_csrf, check_rate_limit
from app.core.errors import PermissionDeniedError
from app.core.events import EventPublisherDep
from app.core.security import Principal
from app.modules.departments.application.dtos import ListDepartmentsQuery
from app.modules.departments.application.services import DepartmentService
from app.modules.departments.infrastructure.repositories import SqlAlchemyDepartmentRepository
from app.modules.departments.interface.schemas import (
    AddMemberRequest,
    CreateDepartmentRequest,
    DepartmentResponse,
    Envelope,
    MembershipResponse,
    PagedEnvelope,
    UpdateDepartmentRequest,
)

router = APIRouter(
    prefix="/departments",
    tags=["departments"],
    dependencies=[Depends(check_rate_limit), Depends(check_csrf)],
)

# Roles that may mutate departments and memberships.
_ADMIN_ROLES = frozenset({"tenant_admin", "platform_admin"})


async def _require_admin(principal: PrincipalDep) -> Principal:
    """Raise 403 unless the principal holds tenant_admin or platform_admin."""
    if not principal.roles & _ADMIN_ROLES:
        raise PermissionDeniedError(f"Requires one of: {sorted(_ADMIN_ROLES)}")
    return principal


AdminDep = Annotated[Principal, Depends(_require_admin)]


async def _service(
    session: SessionDep,
    publisher: EventPublisherDep,
) -> DepartmentService:
    return DepartmentService(
        repo=SqlAlchemyDepartmentRepository(session),
        publisher=publisher,
    )


ServiceDep = Annotated[DepartmentService, Depends(_service)]


# ---------------------------------------------------------------------------
# Read endpoints — any authenticated tenant member
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=PagedEnvelope[DepartmentResponse],
    summary="List departments in the organization",
)
async def list_departments(
    principal: PrincipalDep,
    service: ServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PagedEnvelope[DepartmentResponse]:
    _ = principal  # authentication guard; any tenant member may list
    page = await service.list(query=ListDepartmentsQuery(limit=limit, offset=offset))
    return PagedEnvelope.from_page(page)


@router.get(
    "/{dept_id}",
    response_model=Envelope[DepartmentResponse],
    summary="Get a department by id",
)
async def get_department(
    dept_id: UUID,
    principal: PrincipalDep,
    service: ServiceDep,
) -> Envelope[DepartmentResponse]:
    _ = principal  # authentication guard
    dept = await service.get(dept_id=dept_id)
    return Envelope(data=DepartmentResponse.from_entity(dept))


# ---------------------------------------------------------------------------
# Write endpoints — tenant_admin or platform_admin only
# ---------------------------------------------------------------------------


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[DepartmentResponse],
    summary="Create a department (tenant_admin)",
)
async def create_department(
    body: CreateDepartmentRequest,
    principal: AdminDep,
    service: ServiceDep,
) -> Envelope[DepartmentResponse]:
    dept = await service.create(command=body.to_command())
    return Envelope(data=DepartmentResponse.from_entity(dept))


@router.patch(
    "/{dept_id}",
    response_model=Envelope[DepartmentResponse],
    summary="Update department name / description / active flag (tenant_admin)",
)
async def update_department(
    dept_id: UUID,
    body: UpdateDepartmentRequest,
    principal: AdminDep,
    service: ServiceDep,
) -> Envelope[DepartmentResponse]:
    dept = await service.update(dept_id=dept_id, command=body.to_command())
    return Envelope(data=DepartmentResponse.from_entity(dept))


@router.post(
    "/{dept_id}/members",
    response_model=Envelope[MembershipResponse],
    summary="Add a Keycloak subject to a department (tenant_admin). Idempotent.",
)
async def add_member(
    dept_id: UUID,
    body: AddMemberRequest,
    principal: AdminDep,
    service: ServiceDep,
) -> Envelope[MembershipResponse]:
    membership = await service.add_member(dept_id=dept_id, subject=body.subject)
    return Envelope(data=MembershipResponse.from_entity(membership))


@router.delete(
    "/{dept_id}/members/{subject}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a Keycloak subject from a department (tenant_admin). Idempotent.",
)
async def remove_member(
    dept_id: UUID,
    subject: Annotated[str, Path(min_length=1, max_length=255)],
    principal: AdminDep,
    service: ServiceDep,
) -> Response:
    await service.remove_member(dept_id=dept_id, subject=subject)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
