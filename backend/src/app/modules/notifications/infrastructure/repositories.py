"""SQLAlchemy-backed repository for the notification read model.

Uses raw SQL via `text()` against UNQUALIFIED table names — the search_path is
already set to the tenant schema by SessionDep / session_for_tenant().

Callers own the transaction boundary; this adapter never calls commit().

asyncpg footgun: never write `:param::type` — SQLAlchemy's named-parameter
parser claims the `::` first.  Type parameters with `bindparam(..., type_=...)`
and cast columns with `CAST(x AS t)` instead.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Boolean, String, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.notifications.application.dtos import NotificationItem

# ---------------------------------------------------------------------------
# Who is notified about a ticket event
# ---------------------------------------------------------------------------
# A user is involved in a ticket when they authored it, hold an active
# assignment on it, or have acted on it before.  `ba` additionally sees the
# triage/acceptance queue — BA is a realm role, not an AssignmentRole, so
# without this clause the persona that drives the whole workflow would receive
# nothing.  Own actions are never notified back to the actor.
#
# The LEFT JOIN carries per-item read state: `r.entry_id IS NULL` means unread.
#
# All 16 ticket actions notify — no whitelist. `updated` / `submission_replaced`
# are genuinely relevant to a participant (an intake rewritten mid-work), and
# `actor <> :subject` already spares the editor their own edit.  If the bell
# turns out noisy in use, the filter is one `AND a.action IN (...)` here.
#
# ponytail: the driving scan is ix_audit_entries_ticket_occurred (0008) walked
# newest-first and filtered; a user involved in nothing scans the log. Fine at
# Pre-MVP volume — revisit with a per-subject index or a materialized feed if
# the audit log outgrows it.
_PARTICIPATION = """
    FROM audit_entries a
    JOIN tickets t ON t.id = a.entity_id
    LEFT JOIN notification_reads r ON r.entry_id = a.id AND r.subject = :subject
    WHERE a.entity_type = 'ticket'
      AND a.actor <> :subject
      AND (
        CAST(t.author_id AS text) = :subject
        OR EXISTS (
          SELECT 1 FROM ticket_assignments asg
          WHERE asg.ticket_id = t.id
            AND asg.subject = :subject
            AND asg.unassigned_at IS NULL
        )
        OR EXISTS (
          SELECT 1 FROM audit_entries mine
          WHERE mine.entity_type = 'ticket'
            AND mine.entity_id = t.id
            AND mine.actor = :subject
        )
        OR (:is_ba AND a.action IN ('submitted', 'acceptance_requested'))
      )
"""

_SUBJECT = bindparam("subject", type_=String)
_IS_BA = bindparam("is_ba", type_=Boolean)

_FEED_SQL = text(
    f"""
    SELECT a.id, a.entity_id AS ticket_id, t.title AS ticket_title,
           a.action, a.actor, a.occurred_at,
           (r.entry_id IS NOT NULL) AS is_read
    {_PARTICIPATION}
      AND (:include_read OR r.entry_id IS NULL)
    ORDER BY a.occurred_at DESC
    LIMIT :limit
    """
).bindparams(_SUBJECT, _IS_BA, bindparam("include_read", type_=Boolean))

# Always the TRUE total unread — independent of `limit` and of include_read,
# because the badge is the one number read at a glance.
_UNREAD_COUNT_SQL = text(
    f"""
    SELECT count(*) AS unread
    {_PARTICIPATION}
      AND r.entry_id IS NULL
    """
).bindparams(_SUBJECT, _IS_BA)

# Idempotent: re-reading an already-read event is a no-op, so the client may
# retry freely.
_MARK_READ_SQL = text(
    """
    INSERT INTO notification_reads (subject, entry_id)
    VALUES (:subject, :entry_id)
    ON CONFLICT (subject, entry_id) DO NOTHING
    """
)


class SqlNotificationsRepository:
    """Concrete notifications repository using raw SQL. Session is caller-owned."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def feed(
        self, *, subject: str, is_ba: bool, include_read: bool, limit: int
    ) -> list[NotificationItem]:
        result = await self._session.execute(
            _FEED_SQL,
            {
                "subject": subject,
                "is_ba": is_ba,
                "include_read": include_read,
                "limit": limit,
            },
        )
        return [
            NotificationItem(
                id=UUID(str(row["id"])),
                ticket_id=UUID(str(row["ticket_id"])),
                ticket_title=row["ticket_title"],
                action=row["action"],
                actor=row["actor"],
                occurred_at=row["occurred_at"],
                is_read=bool(row["is_read"]),
            )
            for row in result.mappings().all()
        ]

    async def unread_count(self, *, subject: str, is_ba: bool) -> int:
        result = await self._session.execute(
            _UNREAD_COUNT_SQL, {"subject": subject, "is_ba": is_ba}
        )
        return int(result.scalar_one())

    async def mark_read(self, *, subject: str, entry_ids: list[UUID]) -> None:
        if not entry_ids:
            return
        await self._session.execute(
            _MARK_READ_SQL,
            [{"subject": subject, "entry_id": eid} for eid in entry_ids],
        )
