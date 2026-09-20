"""FastAPI router for /api/v1/tickets.

RBAC model (open access + mandatory triage, per the domain brief):
- ANY authenticated tenant member may create/read tickets.
- Fine-grained rules (author-only edits, ba-only triage, per-object
  business_owner/executor rights, no-admin-fallback attestations) live in
  TicketService — the router only passes ``roles`` and identity through.

Composition root: TicketService is wired here with the SQL repository, the
attestation-backed gates, the department/template seams, the intake_templates
SubmissionValidator, and the Phase-1 NullTrackerGateway.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.core.deps import PrincipalDep, SessionDep, check_csrf, check_rate_limit
from app.core.events import EventPublisherDep
from app.core.security import AuthError, Principal
from app.modules.intake_templates.application.services import TemplateService
from app.modules.intake_templates.infrastructure.repositories import (
    SqlAlchemyTemplateRepository,
)
from app.modules.tickets.application.dtos import (
    CloseCommand,
    FinishWorkCommand,
    ListTicketsQuery,
    StartWorkCommand,
    SubmitCommand,
)
from app.modules.tickets.application.services import TicketService
from app.modules.tickets.domain.entities import TicketStatus
from app.modules.tickets.infrastructure.adapters import (
    NullTrackerGateway,
    SqlDepartmentLookup,
    SqlTemplateVersionInfo,
)
from app.modules.tickets.infrastructure.gates import (
    AttestationAcceptanceGate,
    AttestationSpecApprovalGate,
)
from app.modules.tickets.infrastructure.repositories import SqlAlchemyTicketRepository
from app.modules.tickets.interface.schemas import (
    AssignmentResponse,
    AssignRequest,
    AttestationResponse,
    AttestBusinessValueRequest,
    AttestFormalDodRequest,
    AttestSpecApprovalRequest,
    CommentRequest,
    CreateTicketRequest,
    Envelope,
    PagedEnvelope,
    RejectRequest,
    ReplaceSubmissionRequest,
    RequestAcceptanceRequest,
    SubmissionResponse,
    TicketDetailResponse,
    TicketResponse,
    TransitionResponse,
    TriageAcceptRequest,
    UpdateTicketRequest,
)

router = APIRouter(
    prefix="/tickets",
    tags=["tickets"],
    dependencies=[Depends(check_rate_limit), Depends(check_csrf)],
)


def _actor_id(principal: Principal) -> UUID:
    try:
        return UUID(principal.subject)
    except ValueError as exc:
        raise AuthError("Token 'sub' is not a UUID") from exc


async def _service(session: SessionDep, publisher: EventPublisherDep) -> TicketService:
    repo = SqlAlchemyTicketRepository(session)
    return TicketService(
        repo=repo,
        validator=TemplateService(
            repo=SqlAlchemyTemplateRepository(session), publisher=publisher
        ),
        dept_lookup=SqlDepartmentLookup(session),
        version_info=SqlTemplateVersionInfo(session),
        spec_gate=AttestationSpecApprovalGate(repo),
        acceptance_gate=AttestationAcceptanceGate(repo),
        tracker=NullTrackerGateway(),
        publisher=publisher,
    )


ServiceDep = Annotated[TicketService, Depends(_service)]


# ---------------------------------------------------------------------------
# Create / read
# ---------------------------------------------------------------------------


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[TicketDetailResponse],
    summary="Create a ticket draft (any member)",
)
async def create_ticket(
    body: CreateTicketRequest, principal: PrincipalDep, service: ServiceDep
) -> Envelope[TicketDetailResponse]:
    ticket, submission, _ = await service.create_ticket(
        actor_id=_actor_id(principal),
        actor_sub=principal.subject,
        roles=principal.roles,
        command=body.to_command(),
    )
    detail = await service.get_ticket(ticket_id=ticket.id)
    _ = submission
    return Envelope(data=TicketDetailResponse.from_detail(detail))


@router.get(
    "",
    response_model=PagedEnvelope[TicketResponse],
    summary="List the organization's tickets",
)
async def list_tickets(
    principal: PrincipalDep,
    service: ServiceDep,
    status_: Annotated[TicketStatus | None, Query(alias="status")] = None,
    department_id: Annotated[UUID | None, Query()] = None,
    mine: Annotated[bool, Query(description="Only tickets I authored")] = False,
    assigned_to_me: Annotated[bool, Query(description="Only tickets assigned to me")] = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PagedEnvelope[TicketResponse]:
    page = await service.list_tickets(
        actor_id=_actor_id(principal),
        actor_sub=principal.subject,
        query=ListTicketsQuery(
            status=status_,
            department_id=department_id,
            mine=mine,
            assigned_to_me=assigned_to_me,
            limit=limit,
            offset=offset,
        ),
    )
    return PagedEnvelope.from_page(page)


@router.get(
    "/{ticket_id}",
    response_model=Envelope[TicketDetailResponse],
    summary="Get full ticket detail",
)
async def get_ticket(
    ticket_id: UUID, principal: PrincipalDep, service: ServiceDep
) -> Envelope[TicketDetailResponse]:
    _ = principal  # authentication guard; org-wide read
    detail = await service.get_ticket(ticket_id=ticket_id)
    return Envelope(data=TicketDetailResponse.from_detail(detail))


@router.get(
    "/{ticket_id}/transitions",
    response_model=Envelope[list[TransitionResponse]],
    summary="Append-only status transition history",
)
async def list_transitions(
    ticket_id: UUID, principal: PrincipalDep, service: ServiceDep
) -> Envelope[list[TransitionResponse]]:
    _ = principal
    transitions = await service.list_transitions(ticket_id=ticket_id)
    return Envelope(data=[TransitionResponse.from_entity(t) for t in transitions])


@router.get(
    "/{ticket_id}/attestations",
    response_model=Envelope[list[AttestationResponse]],
    summary="Gate attestation records by cycle",
)
async def list_attestations(
    ticket_id: UUID, principal: PrincipalDep, service: ServiceDep
) -> Envelope[list[AttestationResponse]]:
    _ = principal
    attestations = await service.list_attestations(ticket_id=ticket_id)
    return Envelope(data=[AttestationResponse.from_entity(a) for a in attestations])


# ---------------------------------------------------------------------------
# Metadata / submission edits
# ---------------------------------------------------------------------------


@router.patch(
    "/{ticket_id}",
    response_model=Envelope[TicketResponse],
    summary="Patch ticket metadata (author / ba / admin; created|triage only)",
)
async def update_ticket(
    ticket_id: UUID,
    body: UpdateTicketRequest,
    principal: PrincipalDep,
    service: ServiceDep,
) -> Envelope[TicketResponse]:
    ticket = await service.update_ticket(
        ticket_id=ticket_id,
        actor_id=_actor_id(principal),
        actor_sub=principal.subject,
        roles=principal.roles,
        command=body.to_command(),
    )
    return Envelope(data=TicketResponse.from_entity(ticket))


@router.put(
    "/{ticket_id}/submission",
    response_model=Envelope[SubmissionResponse],
    summary="Replace the intake submission with a new version (status=created only)",
)
async def replace_submission(
    ticket_id: UUID,
    body: ReplaceSubmissionRequest,
    principal: PrincipalDep,
    service: ServiceDep,
) -> Envelope[SubmissionResponse]:
    submission = await service.replace_submission(
        ticket_id=ticket_id,
        actor_id=_actor_id(principal),
        actor_sub=principal.subject,
        roles=principal.roles,
        command=body.to_command(),
    )
    return Envelope(data=SubmissionResponse.from_entity(submission))


# ---------------------------------------------------------------------------
# Workflow transitions
# ---------------------------------------------------------------------------


@router.post(
    "/{ticket_id}/submit",
    response_model=Envelope[TicketResponse],
    summary="Edge 1: created → triage (author / ba / admin)",
)
async def submit(
    ticket_id: UUID, principal: PrincipalDep, service: ServiceDep
) -> Envelope[TicketResponse]:
    ticket, _ = await service.submit(
        ticket_id=ticket_id,
        actor_id=_actor_id(principal),
        actor_sub=principal.subject,
        roles=principal.roles,
        command=SubmitCommand(),
    )
    return Envelope(data=TicketResponse.from_entity(ticket))


@router.post(
    "/{ticket_id}/triage-accept",
    response_model=Envelope[TicketResponse],
    summary="Edge 2: triage → spec_approval (ba / admin)",
)
async def triage_accept(
    ticket_id: UUID,
    body: TriageAcceptRequest,
    principal: PrincipalDep,
    service: ServiceDep,
) -> Envelope[TicketResponse]:
    ticket, _, _ = await service.triage_accept(
        ticket_id=ticket_id,
        actor_sub=principal.subject,
        roles=principal.roles,
        command=body.to_command(),
    )
    return Envelope(data=TicketResponse.from_entity(ticket))


@router.post(
    "/{ticket_id}/return-for-refinement",
    response_model=Envelope[TicketResponse],
    summary="Edge 3: triage → created (ba / admin; comment mandatory)",
)
async def return_for_refinement(
    ticket_id: UUID,
    body: CommentRequest,
    principal: PrincipalDep,
    service: ServiceDep,
) -> Envelope[TicketResponse]:
    ticket, _, _ = await service.return_for_refinement(
        ticket_id=ticket_id,
        actor_sub=principal.subject,
        roles=principal.roles,
        command=body.to_return_for_refinement(),
    )
    return Envelope(data=TicketResponse.from_entity(ticket))


@router.post(
    "/{ticket_id}/reject",
    response_model=Envelope[TicketResponse],
    summary="Edges 4/10: triage|acceptance → rejected (ba / admin)",
)
async def reject(
    ticket_id: UUID,
    body: RejectRequest,
    principal: PrincipalDep,
    service: ServiceDep,
) -> Envelope[TicketResponse]:
    ticket, _, _ = await service.reject(
        ticket_id=ticket_id,
        actor_sub=principal.subject,
        roles=principal.roles,
        command=body.to_command(),
    )
    return Envelope(data=TicketResponse.from_entity(ticket))


@router.post(
    "/{ticket_id}/assign",
    response_model=Envelope[AssignmentResponse],
    summary="Assign or replace business_owner / executor (ba / admin)",
)
async def assign(
    ticket_id: UUID,
    body: AssignRequest,
    principal: PrincipalDep,
    service: ServiceDep,
) -> Envelope[AssignmentResponse]:
    assignment = await service.assign(
        ticket_id=ticket_id,
        actor_sub=principal.subject,
        roles=principal.roles,
        command=body.to_command(),
    )
    return Envelope(data=AssignmentResponse.from_entity(assignment))


@router.post(
    "/{ticket_id}/attest/spec-approval",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[AttestationResponse],
    summary="Sign spec_approved attestation (ba only — no admin fallback)",
)
async def attest_spec_approval(
    ticket_id: UUID,
    body: AttestSpecApprovalRequest,
    principal: PrincipalDep,
    service: ServiceDep,
) -> Envelope[AttestationResponse]:
    attestation = await service.attest_spec_approval(
        ticket_id=ticket_id,
        actor_sub=principal.subject,
        roles=principal.roles,
        command=body.to_command(),
    )
    return Envelope(data=AttestationResponse.from_entity(attestation))


@router.post(
    "/{ticket_id}/start-work",
    response_model=Envelope[TicketResponse],
    summary="Edge 5: spec_approval → in_progress (ba / admin / executor; gated)",
)
async def start_work(
    ticket_id: UUID, principal: PrincipalDep, service: ServiceDep
) -> Envelope[TicketResponse]:
    ticket, _ = await service.start_work(
        ticket_id=ticket_id,
        actor_id=_actor_id(principal),
        actor_sub=principal.subject,
        roles=principal.roles,
        command=StartWorkCommand(),
    )
    return Envelope(data=TicketResponse.from_entity(ticket))


@router.post(
    "/{ticket_id}/finish-work",
    response_model=Envelope[TicketResponse],
    summary="Edge 6: in_progress → change_capture (executor / ba / admin)",
)
async def finish_work(
    ticket_id: UUID, principal: PrincipalDep, service: ServiceDep
) -> Envelope[TicketResponse]:
    ticket, _ = await service.finish_work(
        ticket_id=ticket_id,
        actor_id=_actor_id(principal),
        actor_sub=principal.subject,
        roles=principal.roles,
        command=FinishWorkCommand(),
    )
    return Envelope(data=TicketResponse.from_entity(ticket))


@router.post(
    "/{ticket_id}/request-acceptance",
    response_model=Envelope[TicketResponse],
    summary="Edge 7: change_capture → acceptance (was/is summary mandatory)",
)
async def request_acceptance(
    ticket_id: UUID,
    body: RequestAcceptanceRequest,
    principal: PrincipalDep,
    service: ServiceDep,
) -> Envelope[TicketResponse]:
    ticket, _ = await service.request_acceptance(
        ticket_id=ticket_id,
        actor_id=_actor_id(principal),
        actor_sub=principal.subject,
        roles=principal.roles,
        command=body.to_command(),
    )
    return Envelope(data=TicketResponse.from_entity(ticket))


@router.post(
    "/{ticket_id}/attest/formal-dod",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[AttestationResponse],
    summary="Sign formal_dod attestation (ba only — no admin fallback)",
)
async def attest_formal_dod(
    ticket_id: UUID,
    body: AttestFormalDodRequest,
    principal: PrincipalDep,
    service: ServiceDep,
) -> Envelope[AttestationResponse]:
    attestation = await service.attest_formal_dod(
        ticket_id=ticket_id,
        actor_sub=principal.subject,
        roles=principal.roles,
        command=body.to_command(),
    )
    return Envelope(data=AttestationResponse.from_entity(attestation))


@router.post(
    "/{ticket_id}/attest/business-value",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[AttestationResponse],
    summary="Sign business_value attestation (active business_owner only)",
)
async def attest_business_value(
    ticket_id: UUID,
    body: AttestBusinessValueRequest,
    principal: PrincipalDep,
    service: ServiceDep,
) -> Envelope[AttestationResponse]:
    attestation = await service.attest_business_value(
        ticket_id=ticket_id,
        actor_sub=principal.subject,
        roles=principal.roles,
        command=body.to_command(),
    )
    return Envelope(data=AttestationResponse.from_entity(attestation))


@router.post(
    "/{ticket_id}/return-to-work",
    response_model=Envelope[TicketResponse],
    summary="Edge 9: acceptance → in_progress (increments acceptance cycle)",
)
async def return_to_work(
    ticket_id: UUID,
    body: CommentRequest,
    principal: PrincipalDep,
    service: ServiceDep,
) -> Envelope[TicketResponse]:
    ticket, _ = await service.return_to_work(
        ticket_id=ticket_id,
        actor_sub=principal.subject,
        roles=principal.roles,
        command=body.to_return_to_work(),
    )
    return Envelope(data=TicketResponse.from_entity(ticket))


@router.post(
    "/{ticket_id}/close",
    response_model=Envelope[TicketResponse],
    summary="Edge 8: acceptance → closed (ba / admin; dual acceptance gate)",
)
async def close(
    ticket_id: UUID, principal: PrincipalDep, service: ServiceDep
) -> Envelope[TicketResponse]:
    ticket, _ = await service.close(
        ticket_id=ticket_id,
        actor_sub=principal.subject,
        roles=principal.roles,
        command=CloseCommand(),
    )
    return Envelope(data=TicketResponse.from_entity(ticket))
