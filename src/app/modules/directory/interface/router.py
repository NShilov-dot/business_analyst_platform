"""Directory API — tenant-scoped user listing for UI pickers.

GET /v1/directory/users
  Lists members of the caller's tenant backed by Keycloak.
  Any authenticated tenant member may call this endpoint.
  When the Keycloak Admin integration is not configured (kc is None) the
  endpoint degrades gracefully and returns an empty list rather than 500.
"""

from __future__ import annotations

from typing import Annotated

import httpx
import structlog
from fastapi import APIRouter, Depends, Query

from app.core.deps import (
    KeycloakAdminOptionalDep,
    PrincipalDep,
    check_csrf,
    check_rate_limit,
)
from app.core.keycloak_admin import KeycloakAdminError
from app.modules.directory.application.dtos import ListDirectoryUsersQuery
from app.modules.directory.application.services import DirectoryService
from app.modules.directory.infrastructure.adapters import KeycloakDirectoryReader
from app.modules.directory.interface.schemas import DirectoryUserResponse, Envelope

logger = structlog.get_logger(__name__)

router = APIRouter(
    prefix="/directory",
    tags=["directory"],
    # check_rate_limit throttles per-user and per-tenant.
    # check_csrf adds Origin/Referer validation on write methods (GET passes through).
    dependencies=[Depends(check_rate_limit), Depends(check_csrf)],
)


@router.get(
    "/users",
    response_model=Envelope[list[DirectoryUserResponse]],
    summary="List tenant users for pickers (name search)",
)
async def list_users(
    principal: PrincipalDep,
    kc: KeycloakAdminOptionalDep,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> Envelope[list[DirectoryUserResponse]]:
    """Return members of the caller's tenant, optionally filtered by name.

    The tenant is resolved from the verified JWT ``tenant_id`` claim — never
    from query parameters or request bodies.

    Args:
        principal: Authenticated caller (injected by BFF session dep).
        kc:        Keycloak Admin client (``None`` when admin is not configured).
        q:         Optional name/username/email prefix search string.
        limit:     Maximum number of results to return (1-200, default 100).
    """
    if kc is None:
        logger.warning(
            "directory.keycloak_admin_unavailable",
            tenant_id=principal.tenant_id,
        )
        return Envelope(data=[])

    reader = KeycloakDirectoryReader(kc)
    service = DirectoryService(reader)
    try:
        users = await service.list_users(
            tenant_id=str(principal.tenant_id),
            query=ListDirectoryUsersQuery(search=q, limit=limit),
        )
    except (KeycloakAdminError, httpx.HTTPError) as exc:
        # The picker is a convenience — a Keycloak hiccup must not 500 the page.
        # Degrade to an empty directory, same as the kc-unavailable path.
        logger.warning(
            "directory.keycloak_query_failed",
            tenant_id=principal.tenant_id,
            error=str(exc),
        )
        return Envelope(data=[])
    return Envelope(data=[DirectoryUserResponse.from_dto(u) for u in users])
