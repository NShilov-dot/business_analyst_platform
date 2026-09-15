"""Ports (abstract contracts) for the notifications application layer.

Concrete implementations live in `infrastructure/repositories.py`.
The application service depends only on these protocols — not on SQLAlchemy.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.modules.notifications.application.dtos import NotificationItem


class NotificationsRepository(Protocol):
    """Read model over audit_entries + per-item read marks."""

    async def feed(
        self, *, subject: str, is_ba: bool, include_read: bool, limit: int
    ) -> list[NotificationItem]:
        """Most recent events on tickets this user is involved in.

        `include_read=False` returns only events they have not dismissed.
        """
        ...

    async def unread_count(self, *, subject: str, is_ba: bool) -> int:
        """Total undismissed events for this user, unbounded by any limit."""
        ...

    async def mark_read(self, *, subject: str, entry_ids: list[UUID]) -> None:
        """Dismiss these events for this user. Idempotent."""
        ...
