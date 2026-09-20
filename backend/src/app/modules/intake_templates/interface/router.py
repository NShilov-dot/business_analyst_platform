"""FastAPI router for /api/v1/templates.

Read endpoints (GET list, GET by id, GET selection-rules) are open to any
authenticated tenant member.  Write endpoints (POST create, PATCH draft fields,
POST publish, POST archive, POST new-draft) require ``ba`` OR ``tenant_admin``.

Rate limiting and CSRF checks mirror the other module routers.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.core.deps import PrincipalDep, SessionDep, check_csrf, check_rate_limit
from app.core.errors import PermissionDeniedError
from app.core.events import EventPublisherDep
from app.core.security import AuthError, Principal
from app.modules.intake_templates.application.dtos import (
    ListTemplatesQuery,
    UpdateDraftFieldsCommand,
)
from app.modules.intake_templates.application.services import TemplateService
from app.modules.intake_templates.infrastructure.repositories import (
    SqlAlchemyTemplateRepository,
)
from app.modules.intake_templates.interface.schemas import (
    CreateTemplateRequest,
    Envelope,
    PagedEnvelope,
    SelectionRuleResponse,
    TemplateDetailResponse,
    TemplateResponse,
    TemplateVersionResponse,
    UpdateDraftFieldsRequest,
)

router = APIRouter(
    prefix="/templates",
    tags=["intake_templates"],
    dependencies=[Depends(check_rate_limit), Depends(check_csrf)],
)

# Roles that may create / mutate templates.
_MANAGE_ROLES = frozenset({"ba", "tenant_admin", "platform_admin"})


async def _require_manager(principal: PrincipalDep) -> Principal:
    """Raise 403 unless the principal holds ``ba`` or ``tenant_admin``."""
    if not principal.roles & _MANAGE_ROLES:
        raise PermissionDeniedError(f"Requires one of: {sorted(_MANAGE_ROLES)}")
    return principal


ManagerDep = Annotated[Principal, Depends(_require_manager)]


async def _service(
    session: SessionDep,
    publisher: EventPublisherDep,
) -> TemplateService:
    return TemplateService(
        repo=SqlAlchemyTemplateRepository(session),
        publisher=publisher,
    )


ServiceDep = Annotated[TemplateService, Depends(_service)]


def _actor_id(principal: Principal) -> UUID:
    try:
        return UUID(principal.subject)
    except ValueError as exc:
        raise AuthError("Token 'sub' is not a UUID") from exc


# ---------------------------------------------------------------------------
# Read endpoints — any authenticated tenant member
# ---------------------------------------------------------------------------


@router.get(
    "/selection-rules",
    response_model=Envelope[list[SelectionRuleResponse]],
    summary="List static §6.4 template-selection rules",
)
async def get_selection_rules(
    principal: PrincipalDep,
    service: ServiceDep,
) -> Envelope[list[SelectionRuleResponse]]:
    """Return the static code-level selection rules from §6.4 of the brief.

    No database read is needed — the rules are compiled constants.
    """
    _ = principal  # authentication guard
    rules = service.get_selection_rules()
    return Envelope(data=[SelectionRuleResponse.from_entity(r) for r in rules])


@router.get(
    "",
    response_model=PagedEnvelope[TemplateResponse],
    summary="List intake templates in the organization",
)
async def list_templates(
    principal: PrincipalDep,
    service: ServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PagedEnvelope[TemplateResponse]:
    _ = principal  # authentication guard; any tenant member may list
    page = await service.list_templates(query=ListTemplatesQuery(limit=limit, offset=offset))
    return PagedEnvelope.from_page(page)


@router.get(
    "/{template_id}",
    response_model=Envelope[TemplateDetailResponse],
    summary="Get a template by id including all versions",
)
async def get_template(
    template_id: UUID,
    principal: PrincipalDep,
    service: ServiceDep,
) -> Envelope[TemplateDetailResponse]:
    _ = principal  # authentication guard
    result = await service.get_template(template_id=template_id)
    return Envelope(data=TemplateDetailResponse.from_entities(result.template, result.versions))


# ---------------------------------------------------------------------------
# Write endpoints — ba or tenant_admin only
# ---------------------------------------------------------------------------


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[TemplateDetailResponse],
    summary="Create a new intake template (ba / tenant_admin)",
)
async def create_template(
    body: CreateTemplateRequest,
    principal: ManagerDep,
    service: ServiceDep,
) -> Envelope[TemplateDetailResponse]:
    """Create a template and its initial draft version 1.

    Draft v1 is pre-populated with all mandatory-core fields so the BA can
    publish immediately or add type-specific fields before publishing.
    """
    result = await service.create_template(command=body.to_command(), actor_id=_actor_id(principal))
    detail = TemplateDetailResponse.from_entities(result.template, [result.version])
    return Envelope(data=detail)


@router.patch(
    "/{template_id}/versions/{version_id}/fields",
    response_model=Envelope[TemplateVersionResponse],
    summary="Replace all fields of a draft version (ba / tenant_admin)",
)
async def update_draft_fields(
    template_id: UUID,
    version_id: UUID,
    body: UpdateDraftFieldsRequest,
    principal: ManagerDep,
    service: ServiceDep,
) -> Envelope[TemplateVersionResponse]:
    """Full replacement of the draft version's field list.

    Raises 409 if the version is not in draft status (published versions are
    immutable — use POST new-draft to create an editable copy).
    """
    _ = principal
    command: UpdateDraftFieldsCommand = body.to_command()
    version = await service.update_draft_fields(
        template_id=template_id, version_id=version_id, command=command
    )
    return Envelope(data=TemplateVersionResponse.from_entity(version))


@router.post(
    "/{template_id}/versions/{version_id}/publish",
    response_model=Envelope[TemplateVersionResponse],
    summary="Publish a draft version (ba / tenant_admin)",
)
async def publish_version(
    template_id: UUID,
    version_id: UUID,
    principal: ManagerDep,
    service: ServiceDep,
) -> Envelope[TemplateVersionResponse]:
    """Transition a draft version to published.

    Validates the mandatory-core invariant before publishing — returns 422 if
    any mandatory-core field key is absent or has required=False.
    """
    _ = principal
    version = await service.publish_version(template_id=template_id, version_id=version_id)
    return Envelope(data=TemplateVersionResponse.from_entity(version))


@router.post(
    "/{template_id}/versions/{version_id}/archive",
    response_model=Envelope[TemplateVersionResponse],
    summary="Archive a published version (ba / tenant_admin)",
)
async def archive_version(
    template_id: UUID,
    version_id: UUID,
    principal: ManagerDep,
    service: ServiceDep,
) -> Envelope[TemplateVersionResponse]:
    """Transition a published version to archived.

    Returns 409 if the template is a system template (``is_system=True``) — the
    built-in free_form template's published version is protected.
    """
    _ = principal
    version = await service.archive_version(template_id=template_id, version_id=version_id)
    return Envelope(data=TemplateVersionResponse.from_entity(version))


@router.post(
    "/{template_id}/new-draft",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[TemplateVersionResponse],
    summary="Create a new draft version from the latest published version (ba / tenant_admin)",
)
async def new_draft_from_published(
    template_id: UUID,
    principal: ManagerDep,
    service: ServiceDep,
) -> Envelope[TemplateVersionResponse]:
    """Create a new draft version whose fields are copied from the latest published version.

    This is how the BA starts a revision to an already-published template while
    the current published version stays in service.
    """
    version = await service.new_draft_from_published(
        template_id=template_id, actor_id=_actor_id(principal)
    )
    return Envelope(data=TemplateVersionResponse.from_entity(version))
