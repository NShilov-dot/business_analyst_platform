"""Service-level tests for ChatIntakeService with a scripted fake LLM.

Covers:
- Session start + turn recording (context accumulates in the repo)
- Draft coercion: unknown keys and nulls from the LLM never reach the draft
- Readiness comes from the validator, NOT the LLM's complete flag
- finalize(): blocked while invalid; creates+submits a ticket when ready;
  session becomes submitted and refuses further messages
- Ownership: another user cannot touch the session (ba may read)
- Disabled/failing LLM path surfaces LLM_UNAVAILABLE and stores nothing
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.modules.ai_structuring.application.dtos import (
    SendMessageCommand,
    StartSessionCommand,
)
from app.modules.ai_structuring.application.services import ChatIntakeService
from app.modules.ai_structuring.domain.entities import (
    ChatMessage,
    ChatSession,
    ChatSessionStatus,
    LlmTurn,
)
from app.modules.ai_structuring.domain.errors import (
    ChatAccessDeniedError,
    ChatSessionClosedError,
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


class ScriptedLlm:
    """Returns queued LlmTurn objects; records what it was asked."""

    def __init__(self, turns: list[LlmTurn]) -> None:
        self.turns = list(turns)
        self.calls: list[list[tuple[str, str]]] = []

    async def complete_turn(
        self, *, system_prompt: str, history: list[tuple[str, str]]
    ) -> LlmTurn:
        assert "problem" in system_prompt  # prompt built from template fields
        self.calls.append(history)
        return self.turns.pop(0)


class FailingLlm:
    async def complete_turn(
        self, *, system_prompt: str, history: list[tuple[str, str]]
    ) -> LlmTurn:
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
) -> ChatIntakeService:
    return ChatIntakeService(
        repo=repo,
        llm=llm,  # type: ignore[arg-type]
        fields_provider=FakeFieldsProvider(),
        validator=RequiredFieldsValidator(),
        ticket_sink=sink,
        publisher=publisher,
        clock=lambda: _TS,
    )


async def _start(service: ChatIntakeService) -> UUID:
    detail = await service.start_session(
        actor_id=_OWNER_ID,
        actor_sub=_OWNER_SUB,
        command=StartSessionCommand(template_version_id=_VERSION_ID),
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
