"""Admin endpoints for tenant + member onboarding.

  POST /v1/admin/tenants  — register an organization (requires platform_admin)
  POST /v1/admin/users    — invite an employee into the CALLER'S tenant
                            (requires tenant_admin)

Cross-tenant invariant: the member's tenant_id is taken from the caller's
verified principal, never from the request body, so a tenant_admin can only add
users to their own tenant. The grantable roles are restricted (schema) to
tenant_user / tenant_admin — a tenant_admin cannot mint a platform_admin.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.core.deps import (
    PrincipalDep,
    check_csrf,
    check_rate_limit,
    require_roles,
)
from app.modules.tenants.interface.providers import ProvisioningDep
from app.modules.tenants.interface.schemas import (
    CreateTenantRequest,
    InviteMemberRequest,
    MemberInvitedResponse,
    TenantCreatedResponse,
)

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    # check_rate_limit throttles per-user (and per-tenant) — admin endpoints create
    # Keycloak users / send emails, so they must not be unbounded. check_csrf adds
    # the Origin/Referer allowlist on these writes.
    dependencies=[Depends(check_rate_limit), Depends(check_csrf)],
)


@router.post(
    "/tenants",
    status_code=status.HTTP_201_CREATED,
    summary="Onboard a new organization (tenant)",
    dependencies=[Depends(require_roles("platform_admin"))],
)
async def create_tenant(
    body: CreateTenantRequest, service: ProvisioningDep
) -> TenantCreatedResponse:
    result = await service.onboard_tenant(
        slug=body.slug, name=body.name, admin_email=str(body.admin_email)
    )
    return TenantCreatedResponse(
        tenant_id=result.tenant_id,
        slug=result.slug,
        keycloak_group_id=result.keycloak_group_id,
        admin_user_id=result.admin_user_id,
    )


@router.post(
    "/users",
    status_code=status.HTTP_201_CREATED,
    summary="Invite an employee into the caller's tenant",
    dependencies=[Depends(require_roles("tenant_admin"))],
)
async def invite_member(
    body: InviteMemberRequest, principal: PrincipalDep, service: ProvisioningDep
) -> MemberInvitedResponse:
    # tenant_id comes from the verified token — NOT the request body.
    result = await service.invite_member(
        tenant_id=principal.tenant_id, email=str(body.email), roles=body.roles
    )
    return MemberInvitedResponse(
        user_id=result.user_id, tenant_id=result.tenant_id, email=result.email
    )
