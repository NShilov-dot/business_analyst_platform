"""DTOs for the audit module.

Plain Python dataclasses — no SQLAlchemy / Pydantic / FastAPI imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, NamedTuple
from uuid import UUID

# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """Immutable read model of a single audit log entry."""

    id: UUID
    entity_type: str
    entity_id: UUID
    action: str
    actor: str
    roles: list[str]
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    request_id: str | None
    occurred_at: datetime


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AppendAuditEntryCommand:
    """Carries all data needed to persist one audit row.

    ``roles`` is always normalised to a sorted list at construction time so
    audit records have a deterministic ordering regardless of call-site order.
    """

    id: UUID
    entity_type: str
    entity_id: UUID
    action: str
    actor: str
    roles: list[str] = field(default_factory=list)
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    request_id: str | None = None
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        # frozen=True prevents direct assignment; use object.__setattr__ to
        # normalise roles in-place without violating the immutability contract.
        object.__setattr__(self, "roles", sorted(self.roles))


# ---------------------------------------------------------------------------
# Queries and paged result
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ListAuditEntriesQuery:
    entity_type: str | None = None
    entity_id: UUID | None = None
    action: str | None = None
    actor: str | None = None
    limit: int = 20
    offset: int = 0


class AuditEntriesPage(NamedTuple):
    items: list[AuditEntry]
    total: int
    limit: int
    offset: int
