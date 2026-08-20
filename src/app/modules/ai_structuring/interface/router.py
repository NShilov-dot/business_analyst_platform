"""FastAPI router for /v1/intake-chat.

Any authenticated tenant member may run their own AI-intake sessions (open
access + mandatory triage — the resulting ticket still goes through triage).
Per-object ownership (only the requester continues their chat) is enforced by
the service; ba/admin get read access for support.

Composition root: the OpenAI gateway is created lazily per request so an
unset OPENAI_API_KEY degrades to 503 LLM_UNAVAILABLE on LLM-calling endpoints
while reads keep working.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.deps import PrincipalDep, SessionDep, TenantDep, check_csrf, check_rate_limit
from app.core.events import EventPublisher, EventPublisherDep, _BoundPublisher, get_default_bus
from app.core.security import AuthError, Principal
from app.core.tenancy import TenantContext, session_for_tenant
from app.modules.ai_structuring.application.services import ChatIntakeService
from app.modules.ai_structuring.domain.entities import ChatAnalysisStatus, DocumentPreAnalysis
from app.modules.ai_structuring.domain.errors import LlmUnavailableError
from app.modules.ai_structuring.domain.ports import LlmPort
from app.modules.ai_structuring.infrastructure.adapters import (
    DocumentServiceTextProvider,
    SqlTemplateFieldsProvider,
    TicketServiceIntakeSink,
)
from app.modules.ai_structuring.infrastructure.openai_gateway import OpenAILlmGateway
from app.modules.ai_structuring.infrastructure.repositories import (
    SqlAlchemyChatSessionRepository,
)
from app.modules.ai_structuring.interface.schemas import (
    ChatSessionResponse,
    Envelope,
    PagedEnvelope,
    SendMessageRequest,
    SessionDetailResponse,
    StartSessionRequest,
    TurnResponse,
)
from app.modules.documents.infrastructure.repositories import SqlAlchemyDocumentRepository
from app.modules.intake_templates.application.services import TemplateService
from app.modules.intake_templates.infrastructure.repositories import (
    SqlAlchemyTemplateRepository,
)
from app.modules.tickets.application.services import TicketService
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

router = APIRouter(
    prefix="/intake-chat",
    tags=["ai_structuring"],
    dependencies=[Depends(check_rate_limit), Depends(check_csrf)],
)


def _actor_id(principal: Principal) -> UUID:
    try:
        return UUID(principal.subject)
    except ValueError as exc:
        raise AuthError("Token 'sub' is not a UUID") from exc


class _DisabledLlm:
    """LlmPort stand-in when OPENAI_API_KEY is unset — read paths still work."""

    async def complete_turn(
        self, *, system_prompt: str, history: list[tuple[str, str]]
    ) -> object:
        raise LlmUnavailableError("AI intake is not configured (OPENAI_API_KEY is not set)")

    async def analyze_documents(
        self, *, system_prompt: str, text: str
    ) -> DocumentPreAnalysis:
        raise LlmUnavailableError("AI intake is not configured (OPENAI_API_KEY is not set)")


def _llm() -> LlmPort:
    if not get_settings().ai_intake_enabled:
        return _DisabledLlm()  # type: ignore[return-value]
    return OpenAILlmGateway.from_settings()


def build_chat_service(session: AsyncSession, publisher: EventPublisher) -> ChatIntakeService:
    """Composition root for ChatIntakeService, shared by the request-scoped
    DI dependency (`_service`) AND the background analysis task (`_run_analysis`)
    so the wiring lives in exactly one place regardless of which AsyncSession
    (request-scoped vs a fresh tenant-scoped one) drives it.
    """
    template_service = TemplateService(
        repo=SqlAlchemyTemplateRepository(session), publisher=publisher
    )
    ticket_repo = SqlAlchemyTicketRepository(session)
    ticket_service = TicketService(
        repo=ticket_repo,
        validator=template_service,
        dept_lookup=SqlDepartmentLookup(session),
        version_info=SqlTemplateVersionInfo(session),
        spec_gate=AttestationSpecApprovalGate(ticket_repo),
        acceptance_gate=AttestationAcceptanceGate(ticket_repo),
        tracker=NullTrackerGateway(),
        publisher=publisher,
    )
    return ChatIntakeService(
        repo=SqlAlchemyChatSessionRepository(session),
        llm=_llm(),
        fields_provider=SqlTemplateFieldsProvider(session),
        validator=template_service,
        ticket_sink=TicketServiceIntakeSink(ticket_service),
        publisher=publisher,
        doc_texts=DocumentServiceTextProvider(SqlAlchemyDocumentRepository(session)),
    )


async def _service(session: SessionDep, publisher: EventPublisherDep) -> ChatIntakeService:
    return build_chat_service(session, publisher)


ServiceDep = Annotated[ChatIntakeService, Depends(_service)]


async def _run_analysis(
    tenant: TenantContext,
    session_id: UUID,
    document_ids: tuple[UUID, ...],
    actor_id: UUID,
    actor_sub: str,
    roles: frozenset[str],
) -> None:
    """Background task body: opens its OWN tenant-scoped session (independent
    of the request's, which is already closed by the time this runs — Starlette
    runs background tasks after the response, i.e. after SessionDep's commit)
    and drives the deferred LLM pass through the same composition root.
    """
    async for bg_session in session_for_tenant(tenant):
        publisher = _BoundPublisher(
            bus=get_default_bus(),
            session=bg_session,
            actor=actor_sub,
            request_id=None,
            roles=sorted(roles),
        )
        service = build_chat_service(bg_session, publisher)
        await service.run_document_analysis(
            session_id=session_id, document_ids=document_ids, actor_id=actor_id, roles=roles
        )


@router.post(
    "/sessions",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[SessionDetailResponse],
    summary="Start an AI-intake chat session",
)
async def start_session(
    body: StartSessionRequest,
    principal: PrincipalDep,
    service: ServiceDep,
    background: BackgroundTasks,
    tenant: TenantDep,
) -> Envelope[SessionDetailResponse]:
    command = body.to_command()
    detail = await service.start_session(
        actor_id=_actor_id(principal),
        actor_sub=principal.subject,
        roles=principal.roles,
        command=command,
    )
    if detail.session.analysis_status == ChatAnalysisStatus.PENDING:
        background.add_task(
            _run_analysis,
            tenant,
            detail.session.id,
            command.document_ids,
            _actor_id(principal),
            principal.subject,
            principal.roles,
        )
    return Envelope(data=SessionDetailResponse.from_detail(detail))


@router.get(
    "/sessions",
    response_model=PagedEnvelope[ChatSessionResponse],
    summary="List my AI-intake sessions",
)
async def list_sessions(
    principal: PrincipalDep,
    service: ServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PagedEnvelope[ChatSessionResponse]:
    page = await service.list_sessions(
        actor_id=_actor_id(principal), limit=limit, offset=offset
    )
    return PagedEnvelope.from_page(page)


@router.get(
    "/sessions/{session_id}",
    response_model=Envelope[SessionDetailResponse],
    summary="Get a session with full message history and draft state",
)
async def get_session(
    session_id: UUID, principal: PrincipalDep, service: ServiceDep
) -> Envelope[SessionDetailResponse]:
    detail = await service.get_session(
        session_id=session_id,
        actor_id=_actor_id(principal),
        roles=principal.roles,
    )
    return Envelope(data=SessionDetailResponse.from_detail(detail))


@router.post(
    "/sessions/{session_id}/messages",
    response_model=Envelope[TurnResponse],
    summary="Send a message; the assistant replies and updates the draft",
)
async def send_message(
    session_id: UUID,
    body: SendMessageRequest,
    principal: PrincipalDep,
    service: ServiceDep,
) -> Envelope[TurnResponse]:
    turn = await service.send_message(
        session_id=session_id,
        actor_id=_actor_id(principal),
        roles=principal.roles,
        command=body.to_command(),
    )
    return Envelope(data=TurnResponse.from_turn(turn))


@router.post(
    "/sessions/{session_id}/finalize",
    response_model=Envelope[SessionDetailResponse],
    summary="Create the ticket from the draft and submit it to triage (human action)",
)
async def finalize(
    session_id: UUID, principal: PrincipalDep, service: ServiceDep
) -> Envelope[SessionDetailResponse]:
    detail = await service.finalize(
        session_id=session_id,
        actor_id=_actor_id(principal),
        actor_sub=principal.subject,
        roles=principal.roles,
    )
    return Envelope(data=SessionDetailResponse.from_detail(detail))


@router.post(
    "/sessions/{session_id}/discard",
    response_model=Envelope[ChatSessionResponse],
    summary="Discard an active session",
)
async def discard(
    session_id: UUID, principal: PrincipalDep, service: ServiceDep
) -> Envelope[ChatSessionResponse]:
    session = await service.discard(
        session_id=session_id,
        actor_id=_actor_id(principal),
        roles=principal.roles,
    )
    return Envelope(data=ChatSessionResponse.from_entity(session))
