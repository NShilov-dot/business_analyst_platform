"""Analytics application service — read-only query surface.

Any authenticated tenant member may call these; no role gate is applied.
Tickets are org-wide readable in this product.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.modules.analytics.application.dtos import (
    ActivityItem,
    ActivityQuery,
    TicketFlow,
    TicketFlowQuery,
)
from app.modules.analytics.application.ports import AnalyticsRepository


@dataclass(slots=True)
class AnalyticsService:
    """Orchestrates read queries against the analytics repository."""

    repo: AnalyticsRepository

    async def ticket_flow(self, *, query: TicketFlowQuery) -> TicketFlow:
        return await self.repo.ticket_flow(weeks=query.weeks)

    async def ticket_activity(self, *, query: ActivityQuery) -> list[ActivityItem]:
        return await self.repo.ticket_activity(limit=query.limit)
