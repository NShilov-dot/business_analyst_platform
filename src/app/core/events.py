"""Domain event bus — synchronous, in-process, fail-closed.

Design decisions (from PRODUCT_MODULES §5 / §4.4):
- No broker, no outbox, no event sourcing.
- Subscribers run in the SAME AsyncSession as the mutation, so the audit row
  is atomic with the domain change (flush in subscriber, commit in SessionDep).
- Handler exceptions propagate — if the audit write fails the whole transaction
  fails (fail-closed, §8 completeness).
- `wire_event_handlers()` is idempotent: call it from main.py startup without
  worrying about double registration on hot reload or test runs.

NOTE: this module imports FastAPI at module scope (see `EventPublisherDep` at
the bottom).  Application services that only need `EventPublisher` or
`NoopPublisher` therefore transitively pull in FastAPI.  This is acceptable
for the current monolith layout.  If isolation becomes necessary, move
`_BoundPublisher` / `_event_publisher_dep` / `EventPublisherDep` to a
separate `core/events_fastapi.py` module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Canonical envelope carried by every domain event
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DomainEvent:
    """Immutable envelope wrapping one domain state-change.

    Fields:
        entity_type  — logical name of the aggregate ("task", "department", …).
        entity_id    — PK of the affected row.
        action       — what happened ("created", "updated", "status_changed", …).
        actor        — Keycloak `sub` (UUID string) or a system sentinel.
        before       — snapshot of relevant fields BEFORE the change, or None.
        after        — snapshot of relevant fields AFTER the change, or None.
        occurred_at  — UTC moment of the event (defaults to now).
        request_id   — correlation id from RequestContextMiddleware; may be None
                       when events are published outside an HTTP request context.
        roles        — caller's role snapshot at the time of the event.
    """

    entity_type: str
    entity_id: UUID
    action: str
    actor: str  # Keycloak sub claim
    before: dict[str, Any] | None = field(default=None)
    after: dict[str, Any] | None = field(default=None)
    occurred_at: datetime = field(
        default_factory=lambda: datetime.now(UTC),
    )
    request_id: str | None = field(default=None)
    roles: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Handler protocol (framework-free)
# ---------------------------------------------------------------------------


class EventHandler(Protocol):
    """An async callable that handles a domain event.

    The handler receives the event and the CALLER's AsyncSession.  It must
    call `session.flush()` if it writes rows, and MUST NOT call `commit()`.
    """

    async def __call__(self, event: DomainEvent, session: AsyncSession) -> None: ...


# ---------------------------------------------------------------------------
# Publisher protocol — what application services receive
# ---------------------------------------------------------------------------


class EventPublisher(Protocol):
    """Thin callable used by application services to emit domain events.

    The concrete implementation binds the bus + session + request_id so
    services do not need to know about any of those.
    """

    async def __call__(
        self,
        entity_type: str,
        entity_id: UUID,
        action: str,
        *,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> None: ...


# ---------------------------------------------------------------------------
# Event bus
# ---------------------------------------------------------------------------


class EventBus:
    """Synchronous in-process dispatcher.

    Handlers are registered once at startup via `register()`.  Each `publish()`
    call invokes them sequentially in registration order.  Exceptions propagate
    immediately — there is no retry or dead-letter mechanism by design.
    """

    def __init__(self) -> None:
        self._handlers: list[EventHandler] = []
        self._registered_names: set[str] = set()

    def register(self, handler: EventHandler) -> None:
        """Register a handler.  Uses the handler's qualified class name as the
        idempotency key so double calls (hot reload, repeated test wiring) do
        not add the same handler twice.
        """
        name = f"{type(handler).__module__}.{type(handler).__qualname__}"
        if name in self._registered_names:
            return
        self._registered_names.add(name)
        self._handlers.append(handler)

    async def publish(self, event: DomainEvent, session: AsyncSession) -> None:
        """Dispatch `event` to all registered handlers in order.

        Raises on the first handler failure (fail-closed).
        """
        for handler in self._handlers:
            await handler(event, session)


# ---------------------------------------------------------------------------
# No-op publisher — use this explicitly where auditing is intentionally skipped
# ---------------------------------------------------------------------------


class NoopPublisher:
    """A publisher that discards all events.

    Pass this explicitly when you need a non-HTTP execution context (e.g. a
    management command or a test that intentionally bypasses auditing).

    IMPORTANT: never use this as a silent default — missing publisher wiring
    should be a loud failure, not a silent no-op.  Prefer making `publisher`
    a required field in service dataclasses and injecting this only when you
    have consciously decided that events should be dropped.
    """

    async def __call__(
        self,
        entity_type: str,
        entity_id: UUID,
        action: str,
        *,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> None:
        pass


# ---------------------------------------------------------------------------
# Module-level default bus
# ---------------------------------------------------------------------------

_default_bus = EventBus()


def get_default_bus() -> EventBus:
    """Return the process-wide singleton bus."""
    return _default_bus


def wire_event_handlers() -> None:
    """Register all known event handlers on the default bus.

    Idempotent: safe to call multiple times (hot-reload, test setUp).
    Must be called once from main.py/lifespan before accepting requests.

    New handlers are added here as modules are built (audit subscriber is first).
    """
    from app.modules.audit.infrastructure.subscriber import AuditEventSubscriber

    _default_bus.register(AuditEventSubscriber())


# ---------------------------------------------------------------------------
# FastAPI dependency: per-request publisher
# ---------------------------------------------------------------------------

# Keep FastAPI import at the bottom so the module above stays importable
# without the web framework (unit tests, background tasks).

from typing import Annotated  # noqa: E402

from fastapi import Depends, Request  # noqa: E402

from app.core.deps import PrincipalDep, SessionDep  # noqa: E402


class _BoundPublisher:
    """Concrete publisher bound to the current request's session, bus and actor."""

    __slots__ = ("_actor", "_bus", "_request_id", "_roles", "_session")

    def __init__(
        self,
        *,
        bus: EventBus,
        session: AsyncSession,
        actor: str,
        request_id: str | None,
        roles: list[str],
    ) -> None:
        self._bus = bus
        self._session = session
        self._actor = actor
        self._request_id = request_id
        self._roles = roles

    async def __call__(
        self,
        entity_type: str,
        entity_id: UUID,
        action: str,
        *,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> None:
        event = DomainEvent(
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            actor=self._actor,
            before=before,
            after=after,
            occurred_at=datetime.now(UTC),
            request_id=self._request_id,
            roles=self._roles,
        )
        await self._bus.publish(event, self._session)


def _get_request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


async def _event_publisher_dep(
    request: Request,
    principal: PrincipalDep,
    session: SessionDep,
) -> EventPublisher:
    """FastAPI dependency that returns a publisher bound to this request."""
    return _BoundPublisher(
        bus=get_default_bus(),
        session=session,
        actor=principal.subject,
        request_id=_get_request_id(request),
        roles=sorted(principal.roles),
    )


EventPublisherDep = Annotated[EventPublisher, Depends(_event_publisher_dep)]
