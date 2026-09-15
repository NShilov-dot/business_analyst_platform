"""Notifications application service — derived feed + per-item read marks.

Every authenticated tenant member reads their OWN feed; there is no role gate
on the endpoint.  The `ba` role widens what a user is shown (the triage /
acceptance queue), it does not grant access to anyone else's feed.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.modules.notifications.application.dtos import (
    NotificationFeed,
    NotificationsQuery,
)
from app.modules.notifications.application.ports import NotificationsRepository

_BA_ROLES: frozenset[str] = frozenset({"ba"})


@dataclass(slots=True)
class NotificationsService:
    """Orchestrates the derived feed and per-item read marks."""

    repo: NotificationsRepository

    async def feed(
        self, *, subject: str, roles: frozenset[str], query: NotificationsQuery
    ) -> NotificationFeed:
        is_ba = bool(roles & _BA_ROLES)
        items = await self.repo.feed(
            subject=subject,
            is_ba=is_ba,
            include_read=query.include_read,
            limit=query.limit,
        )
        # Counted separately so the badge stays the true total: `items` is
        # capped by `limit` and may exclude read events entirely.
        unread = await self.repo.unread_count(subject=subject, is_ba=is_ba)
        return NotificationFeed(items=tuple(items), unread_count=unread)

    async def mark_read(self, *, subject: str, entry_ids: list[UUID]) -> None:
        """Dismiss the given events for this user.

        Ids are not checked against the user's participation: the write is
        self-scoped (subject comes from the verified token), inserts nothing
        readable by anyone else, and a membership probe would buy no security.
        The request schema caps the batch size.
        """
        await self.repo.mark_read(subject=subject, entry_ids=entry_ids)
