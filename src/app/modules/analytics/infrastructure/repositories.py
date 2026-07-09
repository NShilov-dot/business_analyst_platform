"""SQLAlchemy-backed repository for analytics aggregations.

Uses raw SQL via `text()` against UNQUALIFIED table names — the search_path
is already set to the tenant schema by SessionDep / session_for_tenant().

Callers own the transaction boundary; this adapter never calls commit().
"""

from __future__ import annotations

import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.analytics.application.dtos import (
    ActivityItem,
    FlowPoint,
    TicketFlow,
)

_TICKET_FLOW_SQL = text(
    """
    WITH weeks AS (
      SELECT generate_series(
        date_trunc('week', now()) - make_interval(weeks => (:weeks)::int - 1),
        date_trunc('week', now()),
        interval '1 week'
      ) AS week_start
    ),
    created AS (
      SELECT date_trunc('week', created_at) AS w, count(*) AS c
      FROM tickets
      GROUP BY 1
    ),
    closed AS (
      -- closed_at is set for BOTH terminal statuses (closed + rejected); the
      -- flow chart's "closed" series must count genuinely-closed tickets only.
      SELECT date_trunc('week', closed_at) AS w, count(*) AS c
      FROM tickets
      WHERE closed_at IS NOT NULL AND status = 'closed'
      GROUP BY 1
    )
    SELECT
      w.week_start::date AS week_start,
      COALESCE(cr.c, 0) AS created,
      COALESCE(cl.c, 0) AS closed
    FROM weeks w
    LEFT JOIN created cr ON cr.w = w.week_start
    LEFT JOIN closed  cl ON cl.w = w.week_start
    ORDER BY w.week_start
    """
)

_TICKET_ACTIVITY_SQL = text(
    """
    SELECT
      a.id AS entry_id,
      a.entity_id,
      a.action,
      a.actor,
      a.occurred_at,
      a.after->>'status' AS status,
      t.title AS ticket_title
    FROM audit_entries a
    LEFT JOIN tickets t ON t.id = a.entity_id
    WHERE a.entity_type = 'ticket'
    ORDER BY a.occurred_at DESC
    LIMIT :limit
    """
)


class SqlAnalyticsRepository:
    """Concrete analytics repository using raw SQL. Session is caller-owned."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def ticket_flow(self, *, weeks: int) -> TicketFlow:
        result = await self._session.execute(
            _TICKET_FLOW_SQL,
            {"weeks": weeks},
        )
        rows = result.mappings().all()
        points = [
            FlowPoint(
                week_start=row["week_start"]
                if isinstance(row["week_start"], datetime.date)
                else datetime.date.fromisoformat(str(row["week_start"])),
                created=int(row["created"]),
                closed=int(row["closed"]),
            )
            for row in rows
        ]
        return TicketFlow(weeks=weeks, points=tuple(points))

    async def ticket_activity(self, *, limit: int) -> list[ActivityItem]:
        result = await self._session.execute(
            _TICKET_ACTIVITY_SQL,
            {"limit": limit},
        )
        rows = result.mappings().all()
        return [
            ActivityItem(
                entry_id=UUID(str(row["entry_id"])),
                entity_id=UUID(str(row["entity_id"])),
                ticket_title=row["ticket_title"],
                action=str(row["action"]),
                status=row["status"],
                actor=str(row["actor"]),
                occurred_at=row["occurred_at"],
            )
            for row in rows
        ]
