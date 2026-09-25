"""FastAPI router for /api/v1/intake-chat.

Any authenticated tenant member may run their own AI-intake sessions (open
access + mandatory triage — the resulting ticket still goes through triage).
Per-object ownership (only the requester continues their chat) is enforced by
the service; ba/admin get read access for support.

Composition root: the OpenAI gateway is created lazily per request so an
unset OPENAI_API_KEY degrades to 503 LLM_UNAVAILABLE on LLM-calling endpoints
while reads keep working.
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.deps import PrincipalDep, SessionDep, TenantDep, check_csrf, check_rate_limit
from app.core.events import EventPublisher, EventPublisherDep, _BoundPublisher, get_default_bus
from app.core.security import AuthError, Principal
from app.core.tenancy import TenantContext, session_for_tenant
from app.modules.ai_structuring.application.services import ChatIntakeService
from app.modules.ai_structuring.domain.entities import (
    ALLOWED_AUDIO_CONTENT_TYPES,
    AUDIO_MAX_BYTES,
    ChatAnalysisStatus,
    DocumentPreAnalysis,
)
from app.modules.ai_structuring.domain.errors import (
    AudioTooLargeError,
    ChatValidationError,
    LlmUnavailableError,
    TranscriptionUnavailableError,
    UnsupportedAudioTypeError,
)
from app.modules.ai_structuring.domain.ports import LlmPort, TranscriptionPort
from app.modules.ai_structuring.infrastructure.adapters import (
    DocumentServiceTextProvider,
    SqlTemplateFieldsProvider,
    TicketServiceIntakeSink,
)
from app.modules.ai_structuring.infrastructure.openai_gateway import OpenAILlmGateway
from app.modules.ai_structuring.infrastructure.repositories import (
    SqlAlchemyChatSessionRepository,
)
from app.modules.ai_structuring.infrastructure.transcription_gateway import (
    ModalTranscriptionGateway,
)
from app.modules.ai_structuring.interface.schemas import (
    ChatSessionResponse,
    Envelope,
    PagedEnvelope,
    RenameSessionRequest,
    SendMessageRequest,
    SessionDetailResponse,
    SpeechRequest,
    StartSessionRequest,
    TranscriptionResponse,
    TurnResponse,
    WarmupResponse,
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

log = logging.getLogger(__name__)

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


class _DisabledTranscriber:
    """TranscriptionPort stand-in when transcription is not configured."""

    async def transcribe(self, *, content: bytes, content_type: str, filename: str) -> str:
        raise TranscriptionUnavailableError(
            "Voice transcription is not configured (TRANSCRIPTION_URL is not set)"
        )

    async def synthesize(self, *, text: str) -> bytes:
        raise TranscriptionUnavailableError(
            "Voice transcription is not configured (TRANSCRIPTION_URL is not set)"
        )

    async def warmup(self) -> None:
        pass


def _transcriber() -> TranscriptionPort:
    """A real FastAPI dependency (unlike `_llm`) so endpoint tests can swap in
    a fake transcriber via app.dependency_overrides[_transcriber]."""
    if not get_settings().transcription_enabled:
        return _DisabledTranscriber()
    return ModalTranscriptionGateway.from_settings()


TranscriberDep = Annotated[TranscriptionPort, Depends(_transcriber)]


_CONTENT_LENGTH_SLACK_BYTES = 64 * 1024  # multipart boundary/headers overhead


def _reject_declared_oversized(request: Request, max_bytes: int) -> None:
    """Cheap honest-client mitigation: reject a declared-oversized upload
    before the multipart parser spools it into memory/a temp file. This is
    NOT the authoritative cap — a chunked-transfer or lying-Content-Length
    client still reaches _read_audio_capped, which enforces the real limit
    while reading; in deployed topologies nginx's client_max_body_size (12m)
    bounds the residual worst case."""
    declared = request.headers.get("content-length")
    if declared is None:
        return
    try:
        size = int(declared)
    except ValueError:
        return
    if size > max_bytes + _CONTENT_LENGTH_SLACK_BYTES:
        raise AudioTooLargeError(f"Audio exceeds the {max_bytes // (1024 * 1024)} MiB limit")


async def _read_audio_capped(file: UploadFile, max_bytes: int) -> bytes:
    """Read the upload stream in chunks, rejecting it as soon as it exceeds
    max_bytes — this route is exempt from the global body-size middleware and
    must enforce its own cap while reading (mirrors documents' _read_capped;
    not shared — the raised error type differs and the two call sites are the
    only ones, not worth a shared helper)."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise AudioTooLargeError(f"Audio exceeds the {max_bytes // (1024 * 1024)} MiB limit")
        chunks.append(chunk)
    return b"".join(chunks)


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

    NEVER raises, and brackets itself with log lines. A crash out here (pool
    exhaustion, an unresolvable tenant, a bug in the composition root) lands in
    the ASGI server's handler where nothing correlates it to the session, so the
    row just sits at analysis_status='pending' until the TTL self-heal 10 minutes
    later. The started/finished pair is what distinguishes "the task never ran"
    from "the task ran and died" — the two have identical symptoms otherwise.
    """
    log.info("ai_chat.analysis_task_started session_id=%s", session_id)
    try:
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
    except Exception:
        log.exception("ai_chat.analysis_task_crashed session_id=%s", session_id)
    else:
        log.info("ai_chat.analysis_task_finished session_id=%s", session_id)


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


@router.patch(
    "/sessions/{session_id}",
    response_model=Envelope[ChatSessionResponse],
    summary="Rename the draft (override the running title)",
)
async def rename_session(
    session_id: UUID,
    body: RenameSessionRequest,
    principal: PrincipalDep,
    service: ServiceDep,
) -> Envelope[ChatSessionResponse]:
    session = await service.rename(
        session_id=session_id,
        actor_id=_actor_id(principal),
        roles=principal.roles,
        title=body.draft_title,
    )
    return Envelope(data=ChatSessionResponse.from_entity(session))


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


@router.post(
    "/transcriptions",
    response_model=Envelope[TranscriptionResponse],
    summary="Transcribe a voice message to text (nothing is persisted)",
)
async def transcribe_voice(
    request: Request,
    file: Annotated[UploadFile, File()],
    transcriber: TranscriberDep,
    principal: PrincipalDep,
) -> Envelope[TranscriptionResponse]:
    # Auth: router-level check_rate_limit -> PrincipalDep -> 401 without a session.
    # Deliberately WITHOUT SessionDep/TenantDep: no DB work happens here, and a
    # pooled connection must not be held through a Modal call that can run for
    # minutes on a cold start.
    log.info("voice transcription requested by %s", principal.subject)
    _reject_declared_oversized(request, AUDIO_MAX_BYTES)
    content_type = (file.content_type or "").split(";")[0].strip().lower()
    if content_type not in ALLOWED_AUDIO_CONTENT_TYPES:
        raise UnsupportedAudioTypeError(f"Unsupported audio content type: {content_type!r}")
    data = await _read_audio_capped(file, AUDIO_MAX_BYTES)
    if not data:
        raise ChatValidationError("Empty audio upload")
    text = await transcriber.transcribe(
        content=data, content_type=content_type, filename=file.filename or "voice"
    )
    return Envelope(data=TranscriptionResponse(text=text))


@router.post(
    "/transcriptions/speech",
    summary="Synthesize the assistant's reply to speech (nothing is persisted)",
)
async def synthesize_speech(
    body: SpeechRequest,
    transcriber: TranscriberDep,
    principal: PrincipalDep,
) -> Response:
    # Auth: router-level check_rate_limit -> PrincipalDep -> 401 without a session.
    # Deliberately WITHOUT SessionDep/TenantDep: no DB work happens here, and a
    # pooled connection must not be held through a Modal call that can run for
    # minutes on a cold start (same reasoning as transcribe_voice).
    log.info("speech synthesis requested by %s", principal.subject)
    if not body.text.strip():
        raise ChatValidationError("Empty text for speech synthesis")
    audio = await transcriber.synthesize(text=body.text)
    return Response(content=audio, media_type="audio/wav")


@router.post(
    "/transcriptions/warmup",
    status_code=202,
    response_model=Envelope[WarmupResponse],
    summary="Prewarm the transcription GPU",
)
async def warmup_transcription(
    background: BackgroundTasks,
    transcriber: TranscriberDep,
    principal: PrincipalDep,
) -> Envelope[WarmupResponse]:
    # Always 202, even when the feature is disabled — the frontend fires this
    # on every chat-page open and must never handle an error from it.
    log.info("transcription warmup requested by %s", principal.subject)
    background.add_task(transcriber.warmup)
    return Envelope(data=WarmupResponse(status="warming"))
