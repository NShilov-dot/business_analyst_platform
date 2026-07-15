"""Ports (abstract contracts) for the analytics application layer.

Concrete implementations live in `infrastructure/repositories.py`.
The application service depends only on these protocols — not on SQLAlchemy.
"""

from __future__ import annotations

from typing import Protocol

from app.modules.analytics.application.dtos import ActivityItem, TicketFlow


class AnalyticsRepository(Protocol):
    """Read-only contract for analytics data aggregation."""

    async def ticket_flow(self, *, weeks: int) -> TicketFlow:
        """Return weekly created/closed counts for the last `weeks` weeks."""
        ...

    async def ticket_activity(self, *, limit: int) -> list[ActivityItem]:
        """Return the most recent ticket lifecycle audit events."""
        ...
