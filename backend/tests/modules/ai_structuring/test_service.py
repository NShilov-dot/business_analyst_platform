"""Service-level tests for ChatIntakeService with a scripted fake LLM.

Covers:
- Session start + turn recording (context accumulates in the repo)
- Draft coercion: unknown keys and nulls from the LLM never reach the draft
- Readiness comes from the validator, NOT the LLM's complete flag
- finalize(): blocked while invalid; creates+submits a ticket when ready;
  session becomes submitted and refuses further messages
- Ownership: another user cannot touch the session (ba may read)
- Disabled/failing LLM path surfaces LLM_UNAVAILABLE and stores nothing
- Documents pre-analysis runs in the BACKGROUND: start_session with documents
  returns fast with analysis_status='pending' and no opener; the LLM pass
  (and its ready/failed outcome) only happens when run_document_analysis is
  called directly, mirroring the router's deferred background task
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.modules.ai_structuring.application.dtos import (
    SendMessageCommand,
    StartSessionCommand,
)
from app.modules.ai_structuring.application.services import ChatIntakeService
from app.modules.ai_structuring.domain.entities import (
    ANALYSIS_PENDING_TTL_SECONDS,
    ChatAnalysisStatus,
    ChatMessage,
    ChatSession,
    ChatSessionStatus,
    DocumentPreAnalysis,
    DocumentText,
    LlmTurn,
)
from app.modules.ai_structuring.domain.errors import (
    ChatAccessDeniedError,
    ChatSessionClosedError,
    ChatValidationError,
    DraftIncompleteError,
    LlmUnavailableError,
)
from app.modules.intake_templates.domain.entities import FieldDefinition, FieldError

_TS = datetime(2026, 7, 7, 10, 0, tzinfo=UTC)
_OWNER_ID = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
_OWNER_SUB = str(_OWNER_ID)
_OTHER_ID = UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")
_BA_ID = UUID("baaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_BA_SUB = str(_BA_ID)
_VERSION_ID = UUID("20000000-0000-0000-0000-000000000001")

_FIELDS = (
    FieldDefinition(key="problem", label="Проблема", kind="textarea", required=True, config={}),
    FieldDefinition(key="metric", label="Метрика", kind="text", required=True, config={}),
    FieldDefinition(key="notes", label="Заметки", kind="text", required=False, config={}),
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeRepo:
    def __init__(self) -> None:
        self.sessions: dict[UUID, ChatSession] = {}
        self.messages: list[ChatMessage] = []
        self.linked: dict[UUID, list[UUID]] = {}

    async def add_session(self, session: ChatSession) -> None:
        self.sessions[session.id] = dataclasses.replace(session)

    async def get_session_by_id(self, session_id: UUID) -> ChatSession | None:
        s = self.sessions.get(session_id)
        return dataclasses.replace(s) if s is not None else None

    async def update_session(self, session: ChatSession) -> None:
        self.sessions[session.id] = dataclasses.replace(session)

    async def list_sessions_for_requester(
        self, requester_id: UUID, *, limit: int, offset: int
    ) -> tuple[list[ChatSession], int]:
        rows = [s for s in self.sessions.values() if s.requester_id == requester_id]
        return rows[offset : offset + limit], len(rows)

    async def add_message(self, message: ChatMessage) -> None:
        self.messages.append(message)

    async def list_messages(self, session_id: UUID) -> list[ChatMessage]:
        return sorted(
            (m for m in self.messages if m.session_id == session_id),
            key=lambda m: m.seq,
        )

    async def link_documents(self, session_id: UUID, document_ids: tuple[UUID, ...]) -> None:
        self.linked.setdefault(session_id, []).extend(document_ids)


class ScriptedLlm:
    """Returns queued LlmTurn objects; records what it was asked."""

    def __init__(
        self, turns: list[LlmTurn], analyses: list[DocumentPreAnalysis] | None = None
    ) -> None:
        self.turns = list(turns)
        self.analyses = list(analyses or [])
        self.calls: list[list[tuple[str, str]]] = []
        self.system_prompts: list[str] = []
        self.analysis_calls: list[str] = []

    async def complete_turn(
        self, *, system_prompt: str, history: list[tuple[str, str]]
    ) -> LlmTurn:
        assert "problem" in system_prompt  # prompt built from template fields
        self.system_prompts.append(system_prompt)
        self.calls.append(history)
        return self.turns.pop(0)

    async def analyze_documents(
        self, *, system_prompt: str, text: str
    ) -> DocumentPreAnalysis:
        assert "problem" in system_prompt  # analysis prompt carries the field schema
        self.analysis_calls.append(text)
        return self.analyses.pop(0)


class FailingLlm:
    async def complete_turn(
        self, *, system_prompt: str, history: list[tuple[str, str]]
    ) -> LlmTurn:
        raise LlmUnavailableError("down")

    async def analyze_documents(
        self, *, system_prompt: str, text: str
    ) -> DocumentPreAnalysis:
        raise LlmUnavailableError("down")


class FakeFieldsProvider:
    async def get_published_fields(
        self, version_id: UUID
    ) -> tuple[FieldDefinition, ...] | None:
        return _FIELDS if version_id == _VERSION_ID else None


class RequiredFieldsValidator:
    """Mimics TemplateService: required fields must be present and non-empty."""

    async def validate_submission(
        self, *, template_version_id: UUID, payload: dict[str, object]
    ) -> list[FieldError]:
        errors = []
        for f in _FIELDS:
            if f.required:
                value = payload.get(f.key)
                if not isinstance(value, str) or not value.strip():
                    errors.append(FieldError(key=f.key, message="Required field is missing"))
        return errors


class FakeTicketSink:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.ticket_id = uuid4()

    async def create_and_submit(
        self,
        *,
        actor_id: UUID,
        actor_sub: str,
        roles: frozenset[str],
        title: str,
        template_version_id: UUID,
        payload: dict[str, object],
    ) -> UUID:
        self.calls.append(
            {
                "title": title,
                "payload": dict(payload),
                "actor_id": actor_id,
                "actor_sub": actor_sub,
                "roles": roles,
            }
        )
        return self.ticket_id


class FakeDocTexts:
    """DocumentTextProvider stub. Empty by default (no documents attached);
    tests exercising the document flow pre-load `.texts`."""

    def __init__(self, texts: tuple[DocumentText, ...] = ()) -> None:
        self.texts = texts
        self.calls: list[tuple[UUID, ...]] = []

    async def get_texts_for_session(
        self, document_ids: tuple[UUID, ...], *, requester_id: UUID, roles: frozenset[str]
    ) -> tuple[DocumentText, ...]:
        self.calls.append(document_ids)
        return self.texts


class RecordingPublisher:
    def __init__(self) -> None:
        self.events: list[str] = []

    async def __call__(
        self,
        entity_type: str,
        entity_id: UUID,
        action: str,
        *,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> None:
        # PII discipline: no chat text in event payloads
        for payload in (before or {}), (after or {}):
            for value in payload.values():
                assert not (isinstance(value, str) and len(value) > 200)
        self.events.append(action)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def repo() -> FakeRepo:
    return FakeRepo()


@pytest.fixture
def sink() -> FakeTicketSink:
    return FakeTicketSink()


@pytest.fixture
def publisher() -> RecordingPublisher:
    return RecordingPublisher()


def _service(
    repo: FakeRepo,
    llm: object,
    sink: FakeTicketSink,
    publisher: RecordingPublisher,
    doc_texts: object | None = None,
) -> ChatIntakeService:
    return ChatIntakeService(
        repo=repo,
        llm=llm,  # type: ignore[arg-type]
        fields_provider=FakeFieldsProvider(),
        validator=RequiredFieldsValidator(),
        ticket_sink=sink,
        publisher=publisher,
        doc_texts=doc_texts if doc_texts is not None else FakeDocTexts(),  # type: ignore[arg-type]
        clock=lambda: _TS,
    )


async def _start(
    service: ChatIntakeService,
    *,
    roles: frozenset[str] = frozenset(),
    document_ids: tuple[UUID, ...] = (),
) -> UUID:
    detail = await service.start_session(
        actor_id=_OWNER_ID,
        actor_sub=_OWNER_SUB,
        roles=roles,
        command=StartSessionCommand(template_version_id=_VERSION_ID, document_ids=document_ids),
    )
    return detail.session.id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_turns_accumulate_context_and_draft(
    repo: FakeRepo, sink: FakeTicketSink, publisher: RecordingPublisher
) -> None:
    llm = ScriptedLlm(
        [
            LlmTurn(
                reply="Какую метрику улучшаем?",
                draft={"problem": "Ручной ввод тарифа", "junk_key": "x"},
                title="Автоподстановка тарифа",
                complete=False,
            ),
            LlmTurn(
                reply="Готово, проверьте черновик.",
                draft={"problem": "Ручной ввод тарифа", "metric": "-30% времени"},
                title="Автоподстановка тарифа",
                complete=True,
            ),
        ]
    )
    service = _service(repo, llm, sink, publisher)
    session_id = await _start(service)

    turn1 = await service.send_message(
        session_id=session_id,
        actor_id=_OWNER_ID,
        roles=frozenset(),
        command=SendMessageCommand(content="Оператор вводит тариф вручную"),
    )
    assert turn1.is_ready is False
    assert turn1.session.draft == {"problem": "Ручной ввод тарифа"}  # junk_key dropped
    assert [f.key for f in turn1.fields if f.missing] == ["metric"]

    turn2 = await service.send_message(
        session_id=session_id,
        actor_id=_OWNER_ID,
        roles=frozenset(),
        command=SendMessageCommand(content="Минус 30% времени подключения"),
    )
    assert turn2.is_ready is True
    assert turn2.session.message_count == 4

    # Second LLM call received the FULL history including the first exchange
    assert len(llm.calls[1]) == 3  # user1, assistant1, user2
    assert llm.calls[1][0] == ("user", "Оператор вводит тариф вручную")
    assert llm.calls[1][1][0] == "assistant"

    detail = await service.get_session(
        session_id=session_id, actor_id=_OWNER_ID, roles=frozenset()
    )
    assert [m.seq for m in detail.messages] == [1, 2, 3, 4]


async def test_readiness_is_validator_not_llm_claim(
    repo: FakeRepo, sink: FakeTicketSink, publisher: RecordingPublisher
) -> None:
    # LLM claims complete=True but the required metric is missing
    llm = ScriptedLlm(
        [LlmTurn(reply="Всё готово!", draft={"problem": "x"}, title=None, complete=True)]
    )
    service = _service(repo, llm, sink, publisher)
    session_id = await _start(service)

    turn = await service.send_message(
        session_id=session_id,
        actor_id=_OWNER_ID,
        roles=frozenset(),
        command=SendMessageCommand(content="проблема"),
    )
    assert turn.is_ready is False

    with pytest.raises(DraftIncompleteError):
        await service.finalize(
            session_id=session_id,
            actor_id=_OWNER_ID,
            actor_sub=_OWNER_SUB,
            roles=frozenset(),
        )
    assert sink.calls == []


async def test_finalize_creates_ticket_and_closes_session(
    repo: FakeRepo, sink: FakeTicketSink, publisher: RecordingPublisher
) -> None:
    llm = ScriptedLlm(
        [
            LlmTurn(
                reply="ок",
                draft={"problem": "Ручной ввод", "metric": "-30%"},
                title="Автоподстановка тарифа",
                complete=True,
            )
        ]
    )
    service = _service(repo, llm, sink, publisher)
    session_id = await _start(service)
    await service.send_message(
        session_id=session_id,
        actor_id=_OWNER_ID,
        roles=frozenset(),
        command=SendMessageCommand(content="..."),
    )

    detail = await service.finalize(
        session_id=session_id,
        actor_id=_OWNER_ID,
        actor_sub=_OWNER_SUB,
        roles=frozenset(),
    )
    assert detail.session.status == ChatSessionStatus.SUBMITTED
    assert detail.session.ticket_id == sink.ticket_id
    assert sink.calls[0]["title"] == "Автоподстановка тарифа"
    assert sink.calls[0]["payload"] == {"problem": "Ручной ввод", "metric": "-30%"}
    assert publisher.events == ["started", "turn_recorded", "finalized"]

    # Closed session refuses further messages
    with pytest.raises(ChatSessionClosedError):
        await service.send_message(
            session_id=session_id,
            actor_id=_OWNER_ID,
            roles=frozenset(),
            command=SendMessageCommand(content="ещё"),
        )


async def test_ownership_enforced(
    repo: FakeRepo, sink: FakeTicketSink, publisher: RecordingPublisher
) -> None:
    service = _service(repo, ScriptedLlm([]), sink, publisher)
    session_id = await _start(service)

    with pytest.raises(ChatAccessDeniedError):
        await service.get_session(
            session_id=session_id, actor_id=_OTHER_ID, roles=frozenset({"tenant_user"})
        )
    # ba may read for support
    detail = await service.get_session(
        session_id=session_id, actor_id=_OTHER_ID, roles=frozenset({"ba"})
    )
    assert detail.session.id == session_id


async def test_llm_failure_stores_nothing(
    repo: FakeRepo, sink: FakeTicketSink, publisher: RecordingPublisher
) -> None:
    service = _service(repo, FailingLlm(), sink, publisher)
    session_id = await _start(service)

    with pytest.raises(LlmUnavailableError):
        await service.send_message(
            session_id=session_id,
            actor_id=_OWNER_ID,
            roles=frozenset(),
            command=SendMessageCommand(content="привет"),
        )
    assert repo.messages == []
    session = await repo.get_session_by_id(session_id)
    assert session is not None and session.message_count == 0


async def test_finalize_by_ba_authors_ticket_as_requester(
    repo: FakeRepo, sink: FakeTicketSink, publisher: RecordingPublisher
) -> None:
    """#3: a BA finalizing the requester's session must author the ticket as the
    REQUESTER (session owner), not as the BA who clicked finalize — the value gate
    and requester-scoped analytics depend on the author being the requester."""
    llm = ScriptedLlm(
        [
            LlmTurn(
                reply="ок",
                draft={"problem": "Ручной ввод", "metric": "-30%"},
                title="Автоподстановка тарифа",
                complete=True,
            )
        ]
    )
    service = _service(repo, llm, sink, publisher)
    session_id = await _start(service)  # session owner = _OWNER_ID
    await service.send_message(
        session_id=session_id,
        actor_id=_OWNER_ID,
        roles=frozenset(),
        command=SendMessageCommand(content="..."),
    )

    # A BA (not the requester) finalizes on the requester's behalf.
    await service.finalize(
        session_id=session_id,
        actor_id=_BA_ID,
        actor_sub=_BA_SUB,
        roles=frozenset({"ba"}),
    )

    assert sink.calls[0]["actor_id"] == _OWNER_ID
    assert sink.calls[0]["actor_sub"] == _OWNER_SUB
    # the BA's elevated roles must NOT tag along — filed as the requester baseline
    assert sink.calls[0]["roles"] == frozenset({"tenant_user"})


# ---------------------------------------------------------------------------
# Documents pre-analysis (start_session with document_ids / send_message)
# ---------------------------------------------------------------------------

_DOC_ID = uuid4()


async def test_start_session_without_documents_is_unchanged(
    repo: FakeRepo, sink: FakeTicketSink, publisher: RecordingPublisher
) -> None:
    """No document_ids -> no opener, documents_context stays None, and the
    prompt sent to the LLM is unaffected (no documents block)."""
    llm = ScriptedLlm(
        [LlmTurn(reply="ок", draft={"problem": "x"}, title=None, complete=False)]
    )
    service = _service(repo, llm, sink, publisher)
    session_id = await _start(service)

    session = await repo.get_session_by_id(session_id)
    assert session is not None
    assert session.documents_context is None
    assert session.message_count == 0
    assert repo.linked == {}

    detail = await service.get_session(session_id=session_id, actor_id=_OWNER_ID, roles=frozenset())
    assert detail.messages == []

    await service.send_message(
        session_id=session_id,
        actor_id=_OWNER_ID,
        roles=frozenset(),
        command=SendMessageCommand(content="..."),
    )
    assert "Материалы, приложенные заказчиком" not in llm.system_prompts[0]


async def test_start_session_with_documents_returns_pending_immediately(
    repo: FakeRepo, sink: FakeTicketSink, publisher: RecordingPublisher
) -> None:
    """start_session no longer calls the LLM: with document_ids it links them,
    runs the cheap synchronous pre-flight (access/usability), flips to
    'pending', and returns FAST with no opener and an empty draft. The actual
    LLM pass is deferred to run_document_analysis (scheduled by the router as
    a background task after this returns)."""
    doc_texts = FakeDocTexts(
        texts=(
            DocumentText(id=_DOC_ID, filename="tz.pdf", status="extracted", text="Текст документа"),
        )
    )
    llm = ScriptedLlm(turns=[], analyses=[])
    service = _service(repo, llm, sink, publisher, doc_texts)

    detail = await service.start_session(
        actor_id=_OWNER_ID,
        actor_sub=_OWNER_SUB,
        roles=frozenset(),
        command=StartSessionCommand(template_version_id=_VERSION_ID, document_ids=(_DOC_ID,)),
    )

    assert repo.linked[detail.session.id] == [_DOC_ID]
    assert doc_texts.calls == [(_DOC_ID,)]
    assert llm.analysis_calls == []  # LLM is NOT called synchronously

    assert detail.session.analysis_status == ChatAnalysisStatus.PENDING
    assert detail.session.documents_context is None
    assert detail.session.draft == {}
    assert detail.session.message_count == 0
    assert detail.messages == []
    assert publisher.events == ["started"]


async def test_run_document_analysis_applies_result_and_marks_ready(
    repo: FakeRepo, sink: FakeTicketSink, publisher: RecordingPublisher
) -> None:
    """The deferred background job: applies the pre-analysis (opener, context,
    pre-filled draft) and flips analysis_status to 'ready'."""
    doc_texts = FakeDocTexts(
        texts=(
            DocumentText(id=_DOC_ID, filename="tz.pdf", status="extracted", text="Текст документа"),
        )
    )
    llm = ScriptedLlm(
        turns=[],
        analyses=[
            DocumentPreAnalysis(
                summary="Сводка по документу",
                opening="Изучил материалы, вижу...",
                draft={"problem": "Проблема из документа"},
            )
        ],
    )
    service = _service(repo, llm, sink, publisher, doc_texts)

    detail = await service.start_session(
        actor_id=_OWNER_ID,
        actor_sub=_OWNER_SUB,
        roles=frozenset(),
        command=StartSessionCommand(template_version_id=_VERSION_ID, document_ids=(_DOC_ID,)),
    )
    assert detail.session.analysis_status == ChatAnalysisStatus.PENDING

    await service.run_document_analysis(
        session_id=detail.session.id,
        document_ids=(_DOC_ID,),
        actor_id=_OWNER_ID,
        roles=frozenset(),
    )
    assert "Текст документа" in llm.analysis_calls[0]  # combined text sent to analyze_documents

    session = await repo.get_session_by_id(detail.session.id)
    assert session is not None
    assert session.analysis_status == ChatAnalysisStatus.READY
    assert session.documents_context == "Сводка по документу"
    assert session.message_count == 1
    # the pre-analysis PRE-FILLED the draft from the document...
    assert session.draft == {"problem": "Проблема из документа"}
    assert session.documents_prefilled_keys == ("problem",)

    messages = await repo.list_messages(detail.session.id)
    assert [m.seq for m in messages] == [1]
    opener = messages[0]
    assert opener.role.value == "assistant"
    assert opener.content == "Изучил материалы, вижу..."
    assert publisher.events == ["started", "analysis_completed"]

    post = await service.get_session(
        session_id=detail.session.id, actor_id=_OWNER_ID, roles=frozenset()
    )
    problem_state = next(f for f in post.fields if f.key == "problem")
    assert problem_state.from_document is True
    assert problem_state.missing is False
    assert problem_state.value == "Проблема из документа"

    # the first REAL turn still gets seq (2, 3) — the opener didn't shift the scheme
    llm.turns.append(LlmTurn(reply="дальше?", draft={}, title=None, complete=False))
    turn = await service.send_message(
        session_id=detail.session.id,
        actor_id=_OWNER_ID,
        roles=frozenset(),
        command=SendMessageCommand(content="давайте продолжим"),
    )
    assert [m.seq for m in await repo.list_messages(detail.session.id)] == [1, 2, 3]
    assert turn.session.message_count == 3
    # the summary is mixed into EVERY subsequent turn's prompt
    assert "Сводка по документу" in llm.system_prompts[0]
    # a turn that returns an empty draft MERGES over the pre-fill — the
    # document-derived value is NOT dropped, and its marker persists.
    assert turn.session.draft.get("problem") == "Проблема из документа"
    assert next(f for f in turn.fields if f.key == "problem").from_document is True


async def test_run_document_analysis_marks_failed_on_llm_error(
    repo: FakeRepo, sink: FakeTicketSink, publisher: RecordingPublisher
) -> None:
    """LLM failure during the background pass never raises — the session
    flips to 'failed' and stays usable without documents context."""
    doc_texts = FakeDocTexts(
        texts=(
            DocumentText(id=_DOC_ID, filename="tz.pdf", status="extracted", text="Текст документа"),
        )
    )
    service = _service(repo, FailingLlm(), sink, publisher, doc_texts)

    detail = await service.start_session(
        actor_id=_OWNER_ID,
        actor_sub=_OWNER_SUB,
        roles=frozenset(),
        command=StartSessionCommand(template_version_id=_VERSION_ID, document_ids=(_DOC_ID,)),
    )
    assert detail.session.analysis_status == ChatAnalysisStatus.PENDING

    await service.run_document_analysis(  # must NOT raise
        session_id=detail.session.id,
        document_ids=(_DOC_ID,),
        actor_id=_OWNER_ID,
        roles=frozenset(),
    )

    session = await repo.get_session_by_id(detail.session.id)
    assert session is not None
    assert session.analysis_status == ChatAnalysisStatus.FAILED
    assert session.documents_context is None
    assert session.message_count == 0
    assert repo.messages == []
    assert publisher.events == ["started", "analysis_failed"]


async def test_send_message_blocked_while_analysis_pending(
    repo: FakeRepo, sink: FakeTicketSink, publisher: RecordingPublisher
) -> None:
    doc_texts = FakeDocTexts(
        texts=(
            DocumentText(id=_DOC_ID, filename="tz.pdf", status="extracted", text="Текст документа"),
        )
    )
    service = _service(repo, ScriptedLlm(turns=[]), sink, publisher, doc_texts)

    detail = await service.start_session(
        actor_id=_OWNER_ID,
        actor_sub=_OWNER_SUB,
        roles=frozenset(),
        command=StartSessionCommand(template_version_id=_VERSION_ID, document_ids=(_DOC_ID,)),
    )
    assert detail.session.analysis_status == ChatAnalysisStatus.PENDING

    with pytest.raises(ChatValidationError):
        await service.send_message(
            session_id=detail.session.id,
            actor_id=_OWNER_ID,
            roles=frozenset(),
            command=SendMessageCommand(content="привет"),
        )


async def test_start_session_documents_access_denied_when_not_all_readable(
    repo: FakeRepo, sink: FakeTicketSink, publisher: RecordingPublisher
) -> None:
    """doc_texts returning fewer items than requested means at least one
    attached document isn't accessible to this requester."""
    doc_texts = FakeDocTexts(texts=())  # simulates a missing/inaccessible document
    service = _service(repo, ScriptedLlm([]), sink, publisher, doc_texts)

    with pytest.raises(ChatAccessDeniedError):
        await service.start_session(
            actor_id=_OWNER_ID,
            actor_sub=_OWNER_SUB,
            roles=frozenset(),
            command=StartSessionCommand(template_version_id=_VERSION_ID, document_ids=(_DOC_ID,)),
        )


async def test_start_session_all_documents_unusable_is_validation_error(
    repo: FakeRepo, sink: FakeTicketSink, publisher: RecordingPublisher
) -> None:
    doc_texts = FakeDocTexts(
        texts=(DocumentText(id=_DOC_ID, filename="scan.pdf", status="failed", text=""),)
    )
    service = _service(repo, ScriptedLlm([]), sink, publisher, doc_texts)

    with pytest.raises(ChatValidationError):
        await service.start_session(
            actor_id=_OWNER_ID,
            actor_sub=_OWNER_SUB,
            roles=frozenset(),
            command=StartSessionCommand(template_version_id=_VERSION_ID, document_ids=(_DOC_ID,)),
        )


async def test_rename_overrides_draft_title(
    repo: FakeRepo, sink: FakeTicketSink, publisher: RecordingPublisher
) -> None:
    service = _service(repo, ScriptedLlm([]), sink, publisher, FakeDocTexts())
    detail = await service.start_session(
        actor_id=_OWNER_ID,
        actor_sub=_OWNER_SUB,
        roles=frozenset(),
        command=StartSessionCommand(template_version_id=_VERSION_ID),
    )
    session = await service.rename(
        session_id=detail.session.id,
        actor_id=_OWNER_ID,
        roles=frozenset(),
        title="  Экспорт клиентской базы  ",
    )
    assert session.draft_title == "Экспорт клиентской базы"  # trimmed


async def test_rename_rejects_blank_title(
    repo: FakeRepo, sink: FakeTicketSink, publisher: RecordingPublisher
) -> None:
    service = _service(repo, ScriptedLlm([]), sink, publisher, FakeDocTexts())
    detail = await service.start_session(
        actor_id=_OWNER_ID,
        actor_sub=_OWNER_SUB,
        roles=frozenset(),
        command=StartSessionCommand(template_version_id=_VERSION_ID),
    )
    with pytest.raises(ChatValidationError):
        await service.rename(
            session_id=detail.session.id, actor_id=_OWNER_ID, roles=frozenset(), title="   "
        )


async def test_get_session_self_heals_stale_pending_analysis(
    repo: FakeRepo, sink: FakeTicketSink, publisher: RecordingPublisher
) -> None:
    """A backend restart kills the in-process analysis task without flipping
    'pending'; get_session (the endpoint the UI polls) declares such an
    orphaned analysis failed once it is older than ANALYSIS_PENDING_TTL_SECONDS,
    while a fresh 'pending' is left alone."""
    doc_texts = FakeDocTexts(
        texts=(
            DocumentText(id=_DOC_ID, filename="tz.pdf", status="extracted", text="Текст документа"),
        )
    )
    service = _service(repo, ScriptedLlm(turns=[], analyses=[]), sink, publisher, doc_texts)
    session_id = await _start(service, document_ids=(_DOC_ID,))

    detail = await service.get_session(session_id=session_id, actor_id=_OWNER_ID, roles=frozenset())
    assert detail.session.analysis_status == ChatAnalysisStatus.PENDING

    stale = repo.sessions[session_id]
    repo.sessions[session_id] = dataclasses.replace(
        stale, updated_at=_TS - timedelta(seconds=ANALYSIS_PENDING_TTL_SECONDS + 1)
    )

    detail = await service.get_session(session_id=session_id, actor_id=_OWNER_ID, roles=frozenset())
    assert detail.session.analysis_status == ChatAnalysisStatus.FAILED
    assert repo.sessions[session_id].analysis_status == ChatAnalysisStatus.FAILED
