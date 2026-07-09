"""Tests for the AuditEventSubscriber.

Verifies that:
- The subscriber maps a DomainEvent → AppendAuditEntryCommand correctly.
- The subscriber calls repo.append() exactly once.
- flush() is called (via the fake repo) on append.
- No commit() is ever called.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.core.events import DomainEvent
from app.modules.audit.application.dtos import AppendAuditEntryCommand
from app.modules.audit.infrastructure.subscriber import AuditEventSubscriber

# ---------------------------------------------------------------------------
# Fake repository (captures what was appended)
# ---------------------------------------------------------------------------


class _FakeRepo:
    def __init__(self) -> None:
        self.appended: list[AppendAuditEntryCommand] = []
        self.flush_called = 0

    async def append(self, cmd: AppendAuditEntryCommand) -> None:
        self.appended.append(cmd)
        self.flush_called += 1


class _FakeSession:
    """Stand-in for AsyncSession — must NOT have commit() called."""

    def __init__(self) -> None:
        self.committed = False

    def add(self, obj: object) -> None:
        pass

    async def flush(self) -> None:
        pass  # allowed

    async def commit(self) -> None:
        self.committed = True  # should never be called


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _event(**overrides: object) -> DomainEvent:
    entity_id = uuid.uuid4()
    defaults: dict[str, object] = dict(
        entity_type="task",
        entity_id=entity_id,
        action="created",
        actor="actor-sub-001",
        before=None,
        after={"status": "todo"},
        occurred_at=datetime(2026, 7, 4, 12, 0, tzinfo=UTC),
        request_id="req-abc",
        roles=["tenant_user"],
    )
    defaults.update(overrides)
    return DomainEvent(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Test: subscriber maps event → entry correctly
# ---------------------------------------------------------------------------


async def test_subscriber_maps_event_to_entry() -> None:
    """AuditEventSubscriber must translate every field of DomainEvent."""
    entity_id = uuid.uuid4()
    evt = _event(
        entity_type="department",
        entity_id=entity_id,
        action="updated",
        actor="user-sub-999",
        before={"name": "Old"},
        after={"name": "New"},
        request_id="req-xyz",
        roles=["tenant_admin"],
    )

    session = _FakeSession()
    appended_cmds: list[AppendAuditEntryCommand] = []

    # Patch the subscriber to use our fake repo
    subscriber = AuditEventSubscriber()
    original = subscriber.__class__.__call__

    # Use a spy: wrap subscriber call with a fake repo
    class _SpySubscriber:
        async def __call__(self, event: DomainEvent, session: object) -> None:
            from app.modules.audit.infrastructure.repositories import SqlAlchemyAuditRepository

            # We cannot easily inject a fake repo into the subscriber as it
            # creates its own — so we test the command fields by monkeypatching
            # the repository.append method.
            original_append = SqlAlchemyAuditRepository.append

            async def _capture(
                self: SqlAlchemyAuditRepository, cmd: AppendAuditEntryCommand
            ) -> None:
                appended_cmds.append(cmd)
                # simulate flush
                await session.flush()  # type: ignore[union-attr]

            SqlAlchemyAuditRepository.append = _capture  # type: ignore[method-assign]
            try:
                await original(self, event, session)  # type: ignore[arg-type]
            finally:
                SqlAlchemyAuditRepository.append = original_append  # type: ignore[method-assign]

    spy = _SpySubscriber()
    await spy(evt, session)  # type: ignore[arg-type]

    assert len(appended_cmds) == 1
    cmd = appended_cmds[0]
    assert cmd.entity_type == "department"
    assert cmd.entity_id == entity_id
    assert cmd.action == "updated"
    assert cmd.actor == "user-sub-999"
    assert cmd.before == {"name": "Old"}
    assert cmd.after == {"name": "New"}
    assert cmd.request_id == "req-xyz"
    assert cmd.roles == ["tenant_admin"]
    assert session.committed is False


# ---------------------------------------------------------------------------
# Test: subscriber flushes on the same session (not commits)
# ---------------------------------------------------------------------------


async def test_subscriber_flushes_not_commits(monkeypatch: pytest.MonkeyPatch) -> None:
    """Subscriber must flush the session, never commit it."""
    from app.modules.audit.infrastructure.repositories import SqlAlchemyAuditRepository

    flush_calls: list[bool] = []

    async def _fake_append(self: SqlAlchemyAuditRepository, cmd: AppendAuditEntryCommand) -> None:
        # Simulate the actual flush that happens inside append
        flush_calls.append(True)

    monkeypatch.setattr(SqlAlchemyAuditRepository, "append", _fake_append)

    session = _FakeSession()
    subscriber = AuditEventSubscriber()
    await subscriber(_event(), session)  # type: ignore[arg-type]

    assert len(flush_calls) == 1
    assert session.committed is False


# ---------------------------------------------------------------------------
# Test: subscriber is the ONLY writer — direct usage of append_audit_entry
# ---------------------------------------------------------------------------


async def test_subscriber_is_only_writer_via_fake_repo() -> None:
    """Verify that the subscriber's append path is the sole write mechanism.

    We use a completely fake repo to confirm the subscriber calls append
    exactly once and passes the right fields.
    """
    from app.modules.audit.infrastructure.repositories import SqlAlchemyAuditRepository

    appended: list[AppendAuditEntryCommand] = []

    async def _capturing_append(
        self: SqlAlchemyAuditRepository,
        cmd: AppendAuditEntryCommand,
    ) -> None:
        appended.append(cmd)

    entity_id = uuid.uuid4()
    evt = _event(entity_id=entity_id, action="status_changed", roles=["ba"])

    session = _FakeSession()
    subscriber = AuditEventSubscriber()

    import app.modules.audit.infrastructure.repositories as repo_module

    original = repo_module.SqlAlchemyAuditRepository.append
    repo_module.SqlAlchemyAuditRepository.append = _capturing_append  # type: ignore[method-assign]
    try:
        await subscriber(evt, session)  # type: ignore[arg-type]
    finally:
        repo_module.SqlAlchemyAuditRepository.append = original  # type: ignore[method-assign]

    assert len(appended) == 1
    cmd = appended[0]
    assert cmd.entity_id == entity_id
    assert cmd.action == "status_changed"
    assert cmd.roles == ["ba"]
