"""Public self-service company registration (Backend-for-Frontend).

POST /v1/signup creates a brand-new tenant and its founding administrator in one
unauthenticated step. The founder then logs in via the normal OIDC flow and
invites employees with POST /v1/admin/users.

This is the ONLY anonymous path that creates a tenant. Keycloak-native self
registration stays disabled (registrationAllowed=false) on purpose: a
Keycloak-created user would carry no tenant_id and could never resolve a tenant.

Hardening — the endpoint is unauthenticated AND expensive (a Keycloak group +
user plus a per-tenant schema migration), so it layers:
  - a per-minute IP burst cap AND a strict per-hour IP cap, both fail-closed, so a
    flooded/unavailable Redis can't turn signup into a provisioning amplifier;
  - an Origin/Referer allowlist (check_csrf);
  - a reserved-slug guard + duplicate-slug → 409 in the service layer;
  - a response that leaks no internal Keycloak IDs;
  - a feature flag (signup_enabled) so invite-only deployments hide it entirely.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, status

from app.core.deps import (
    SettingsDep,
    check_anon_rate_limit,
    check_csrf,
    check_signup_rate_limit,
)
from app.core.errors import NotFoundError
from app.modules.tenants.interface.providers import ProvisioningDep
from app.modules.tenants.interface.schemas import SignupRequest, SignupResponse

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["signup"])


@router.post(
    "/signup",
    status_code=status.HTTP_201_CREATED,
    summary="Register a new company (self-service)",
    dependencies=[
        Depends(check_anon_rate_limit),
        Depends(check_signup_rate_limit),
        Depends(check_csrf),
    ],
)
async def signup(
    body: SignupRequest, service: ProvisioningDep, settings: SettingsDep
) -> SignupResponse:
    if not settings.signup_enabled:
        # Hide the surface entirely on invite-only / sales-led deployments.
        raise NotFoundError("Self-service signup is not available")

    result = await service.register_tenant(
        slug=body.slug,
        name=body.company_name,
        admin_email=str(body.admin_email),
        admin_password=body.admin_password.get_secret_value(),
        require_email_verification=settings.signup_require_email_verification,
    )
    logger.info("signup.completed", tenant_id=str(result.tenant_id), slug=result.slug)
    return SignupResponse(
        tenant_id=result.tenant_id,
        slug=result.slug,
        # The SPA sends the founder straight into the OIDC login after signup.
        login_url="/v1/auth/login?return_to=/",
    )
