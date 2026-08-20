"""Shared FastAPI providers for tenant provisioning.

Both the admin router (platform-admin onboarding / member invites) and the public
signup router build the same TenantProvisioningService, so the factory lives here
to avoid duplicating wiring and to give tests a single override target.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.db import get_sessionmaker
from app.core.deps import KeycloakAdminDep, ObjectStoreOptionalDep, SettingsDep
from app.modules.tenants.application.services import TenantProvisioningService
from app.modules.tenants.infrastructure.schema_migrator import run_tenant_migrations


def provisioning_service(
    kc: KeycloakAdminDep, settings: SettingsDep, store: ObjectStoreOptionalDep
) -> TenantProvisioningService:
    return TenantProvisioningService(
        sessionmaker=get_sessionmaker(),
        kc=kc,
        migrate_schema=run_tenant_migrations,
        invite_client_id=settings.oidc_client_id,
        object_store=store,
    )


ProvisioningDep = Annotated[TenantProvisioningService, Depends(provisioning_service)]
