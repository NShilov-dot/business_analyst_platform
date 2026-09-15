"""DTOs for the notifications module.

Plain Python dataclasses — no SQLAlchemy / Pydantic / FastAPI imports.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class NotificationsQuery:
    limit: int
    include_read: bool


@dataclass(frozen=True, slots=True)
class NotificationItem:
    """One ticket lifecycle event addressed to the current user.

    Projected deliberately narrow: the source `audit_entries` row also carries
    `before`/`after` JSONB snapshots, `roles` and `request_id`, and /v1/audit is
    gated to tenant_admin BECAUSE of those.  Echoing them here would hand every
    tenant_user field-level diffs through a back door.
    """

    id: UUID  # audit entry id — the key the client marks read
    ticket_id: UUID
    ticket_title: str | None
    action: str
    actor: str
    occurred_at: datetime.datetime
    is_read: bool


@dataclass(frozen=True, slots=True)
class NotificationFeed:
    items: tuple[NotificationItem, ...]
    unread_count: int
