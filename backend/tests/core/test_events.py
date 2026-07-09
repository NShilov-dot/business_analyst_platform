"""Tests for core/events.py.

Covers:
- DomainEvent is frozen (immutable)
- EventBus dispatches to all handlers in registration order
- EventBus propagates exceptions (fail-closed)
- wire_event_handlers() is idempotent (does not double-register)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.core.events import DomainEvent, EventBus, get_default_bus, wire_event_handlers

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _event(**overrides: object) -> DomainEvent:
    defaults: dict[str, object] = dict(
        entity_type="task",
        entity_id=uuid.uuid4(),
        action="created",
        actor="user-sub-001",
        before=None,
        after={"title": "Test"},
        occurred_at=datetime.now(UTC),
        request_id="req-1",
        roles=["tenant_user"],
    )
    defaults.update(overrides)
    return DomainEvent(**defaults)  # type: ignore[arg-type]


class _FakeSession:
    """Minimal stand-in for AsyncSession (we don't call any methods)."""


# ---------------------------------------------------------------------------
# DomainEvent
# ---------------------------------------------------------------------------


def test_domain_event_is_frozen() -> None:
    evt = _event()
    with pytest.raises((AttributeError, TypeError)):
        evt.action = "mutated"  # type: ignore[misc]


def test_domain_event_defaults_occurred_at_to_utc_now() -> None:
    before = datetime.now(UTC)
    evt = DomainEvent(
        entity_type="task",
        entity_id=uuid.uuid4(),
        action="created",
        actor="sub",
    )
    after = datetime.now(UTC)
    assert before <= evt.occurred_at <= after


def test_domain_event_roles_defaults_to_empty() -> None:
    evt = DomainEvent(
        entity_type="task",
        entity_id=uuid.uuid4(),
        action="created",
        actor="sub",
    )
    assert evt.roles == []


# ---------------------------------------------------------------------------
# EventBus — dispatch order
# ---------------------------------------------------------------------------


async def test_event_bus_calls_handlers_in_order() -> None:
    bus = EventBus()
    calls: list[str] = []

    class _H1:
        async def __call__(self, event: DomainEvent, session: object) -> None:
            calls.append("h1")

    class _H2:
        async def __call__(self, event: DomainEvent, session: object) -> None:
            calls.append("h2")

    bus.register(_H1())
    bus.register(_H2())

    session = _FakeSession()
    await bus.publish(_event(), session)  # type: ignore[arg-type]
    assert calls == ["h1", "h2"]


async def test_event_bus_no_handlers_is_noop() -> None:
    bus = EventBus()
    # Should not raise
    await bus.publish(_event(), _FakeSession())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# EventBus — fail-closed (exception propagation)
# ---------------------------------------------------------------------------


async def test_event_bus_propagates_handler_exception() -> None:
    bus = EventBus()
    calls: list[str] = []

    class _Failing:
        async def __call__(self, event: DomainEvent, session: object) -> None:
            raise RuntimeError("audit write failed")

    class _AfterFailing:
        async def __call__(self, event: DomainEvent, session: object) -> None:
            calls.append("should_not_run")

    bus.register(_Failing())
    bus.register(_AfterFailing())

    with pytest.raises(RuntimeError, match="audit write failed"):
        await bus.publish(_event(), _FakeSession())  # type: ignore[arg-type]

    # Second handler must NOT have been called (fail-fast, not fan-out-all)
    assert calls == []


# ---------------------------------------------------------------------------
# Idempotent wiring
# ---------------------------------------------------------------------------


def test_wire_event_handlers_is_idempotent() -> None:
    """Calling wire_event_handlers() twice must not double-register handlers."""
    bus = get_default_bus()
    count_before = len(bus._handlers)

    wire_event_handlers()
    count_mid = len(bus._handlers)
    # count_mid must be >= count_before (first call may have registered new handlers)
    assert count_mid >= count_before

    # Second call must be a strict no-op — count must not grow.
    wire_event_handlers()
    assert len(bus._handlers) == count_mid


def test_event_bus_idempotent_registration() -> None:
    """register() with the same handler type twice must add it only once."""
    bus = EventBus()

    class _H:
        async def __call__(self, event: DomainEvent, session: object) -> None:
            pass

    h = _H()
    bus.register(h)
    bus.register(h)

    assert len(bus._handlers) == 1


# ---------------------------------------------------------------------------
# AsyncMock usage sanity-check (future extensibility test)
# ---------------------------------------------------------------------------


async def test_event_bus_with_async_mock_handler() -> None:
    """Ensure the bus correctly awaits an async mock handler."""
    bus = EventBus()
    mock_handler = AsyncMock()

    # AsyncMock is callable but its class name would not collide with itself
    # through the idempotency guard — we register the mock directly.
    bus._handlers.append(mock_handler)

    session = _FakeSession()
    evt = _event()
    await bus.publish(evt, session)  # type: ignore[arg-type]

    mock_handler.assert_awaited_once_with(evt, session)
