"""Integration tests for SqlAnalyticsRepository.

Uses a real Postgres container (via testcontainers) with the full tenant
schema applied through Alembic migrations.  The container and schema are
provisioned once per test session; each test gets its own engine + session
(function-scoped) so the asyncio event loop never outlives a connection.
All inserts are rolled back after each test to keep tests isolated.

Covered:
- ticket_flow: zero-filled week series, correct created/closed buckets,
  inactive-week points are 0 not absent.
- ticket_activity: only 'ticket' entity_type rows returned, most-recent-first,
  limited by <limit>, ticket_title joined from tickets (None when no match),
  status extracted from after->>'status'.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ---------------------------------------------------------------------------
# Ensure env vars exist before any app imports (alembic/env.py calls
# get_settings() at import time).
# ---------------------------------------------------------------------------
os.environ.setdefault("APP_ENV", "local")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("KEYCLOAK_ISSUER", "http://localhost:8080/realms/bap")
os.environ.setdefault("KEYCLOAK_AUDIENCE", "bap-backend")


# ---------------------------------------------------------------------------
# Session-scoped container + migration fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def _pg_url() -> str:  # type: ignore[return]
    """Start a Postgres container once and return the asyncpg URL.

    The container lifetime is tied to the test session via the context-manager
    yield; testcontainers shuts it down after all tests complete.
    """
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine") as pg:
        sync_url: str = pg.get_connection_url()
        async_url = sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)

        # Point get_settings() at this container so alembic env.py works.
        os.environ["DATABASE_URL"] = async_url

        # Reload app.config so the cached Settings pick up the new DATABASE_URL.
        import importlib

        import app.config as _cfg

        importlib.reload(_cfg)

        # Run migrations (sync Alembic API — runs its own asyncio.run internally).
        from alembic import command as alembic_cmd
        from alembic.config import Config

        pub_cfg = Config("alembic.ini")
        pub_cfg.cmd_opts = argparse.Namespace(x=["scope=public"])
        alembic_cmd.upgrade(pub_cfg, "public@head")

        tenant_cfg = Config("alembic.ini")
        tenant_cfg.cmd_opts = argparse.Namespace(x=["scope=tenant", "schema=tenant_analytics_test"])
        alembic_cmd.upgrade(tenant_cfg, "tenant@head")

        yield async_url  # container alive for whole session


# ---------------------------------------------------------------------------
# Function-scoped session fixture (new engine per test → clean event loop)
# ---------------------------------------------------------------------------

_SCHEMA = "tenant_analytics_test"


@pytest.fixture
async def session(_pg_url: str) -> AsyncIterator[AsyncSession]:
    """Yield an AsyncSession scoped to the tenant schema.

    A fresh engine is created per test so that async connections are always
    bound to the current event loop (pytest-asyncio uses a new loop per test
    in the default ``asyncio_mode=auto`` configuration).

    All writes are rolled back on teardown so tests do not interfere.
    """
    engine = create_async_engine(_pg_url, echo=False, pool_size=1, max_overflow=0)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    try:
        async with factory() as sess:
            await sess.execute(text(f'SET LOCAL search_path TO "{_SCHEMA}", public'))
            try:
                yield sess
            finally:
                await sess.rollback()
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UTC = datetime.UTC


def _monday(offset_weeks: int = 0) -> datetime.datetime:
    """Return midnight UTC on the Monday of (current_week + offset_weeks)."""
    today = datetime.date.today()
    # isoweekday(): Monday=1 … Sunday=7
    monday = today - datetime.timedelta(days=today.isoweekday() - 1)
    monday += datetime.timedelta(weeks=offset_weeks)
    return datetime.datetime(monday.year, monday.month, monday.day, tzinfo=_UTC)


async def _insert_ticket(
    session: AsyncSession,
    *,
    ticket_id: uuid.UUID | None = None,
    title: str = "Test ticket",
    status: str = "created",
    created_at: datetime.datetime | None = None,
    closed_at: datetime.datetime | None = None,
) -> uuid.UUID:
    """Insert a minimal tickets row and return its id."""
    tid = ticket_id or uuid.uuid4()
    ts = created_at or _monday()
    await session.execute(
        text(
            "INSERT INTO tickets "
            "(id, title, status, author_id, created_at, updated_at, closed_at) "
            "VALUES (:id, :title, :status, :author_id, :created_at, :updated_at, :closed_at)"
        ),
        {
            "id": tid,
            "title": title,
            "status": status,
            "author_id": uuid.uuid4(),
            "created_at": ts,
            "updated_at": ts,
            "closed_at": closed_at,
        },
    )
    await session.flush()
    return tid


async def _insert_audit_entry(
    session: AsyncSession,
    *,
    entry_id: uuid.UUID | None = None,
    entity_type: str = "ticket",
    entity_id: uuid.UUID | None = None,
    action: str = "created",
    actor: str = "test-actor",
    after: dict | None = None,
    occurred_at: datetime.datetime | None = None,
) -> uuid.UUID:
    """Insert an audit_entries row and return its id.

    The ``after`` JSONB column cannot use ``:param::jsonb`` with asyncpg
    (the ``:`` triggers named-parameter parsing before the ``::`` cast is
    seen).  We pass the JSON string and use ``CAST(:after_json AS jsonb)``
    instead, which is portable and avoids the parser clash.
    """
    eid = entry_id or uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO audit_entries "
            "(id, entity_type, entity_id, action, actor, after, occurred_at) "
            "VALUES (:id, :entity_type, :entity_id, :action, :actor, "
            "CAST(:after_json AS jsonb), :occurred_at)"
        ),
        {
            "id": eid,
            "entity_type": entity_type,
            "entity_id": entity_id or uuid.uuid4(),
            "action": action,
            "actor": actor,
            "after_json": json.dumps(after) if after is not None else None,
            "occurred_at": occurred_at or _monday(),
        },
    )
    await session.flush()
    return eid


# ---------------------------------------------------------------------------
# ticket_flow tests
# ---------------------------------------------------------------------------


async def test_ticket_flow_returns_exactly_n_weeks(session: AsyncSession) -> None:
    """ticket_flow(weeks=N) always returns a list of exactly N FlowPoints."""
    from app.modules.analytics.infrastructure.repositories import SqlAnalyticsRepository

    repo = SqlAnalyticsRepository(session)

    for n in (1, 4, 8):
        result = await repo.ticket_flow(weeks=n)
        assert result.weeks == n
        assert len(result.points) == n, f"Expected {n} points, got {len(result.points)}"


async def test_ticket_flow_points_are_ascending(session: AsyncSession) -> None:
    """week_start values must be strictly ascending."""
    from app.modules.analytics.infrastructure.repositories import SqlAnalyticsRepository

    repo = SqlAnalyticsRepository(session)
    result = await repo.ticket_flow(weeks=6)
    dates = [p.week_start for p in result.points]
    assert dates == sorted(dates), "FlowPoints are not in ascending order"


async def test_ticket_flow_empty_weeks_are_zero(session: AsyncSession) -> None:
    """Weeks with no ticket activity appear with created=0 and closed=0."""
    from app.modules.analytics.infrastructure.repositories import SqlAnalyticsRepository

    # Insert one ticket in the current week only; 4-week window → 3 zero weeks.
    await _insert_ticket(session, created_at=_monday(0))

    repo = SqlAnalyticsRepository(session)
    result = await repo.ticket_flow(weeks=4)

    assert len(result.points) == 4

    # The last point (current week) must have created >= 1.
    current_week_point = result.points[-1]
    assert current_week_point.created >= 1

    # All earlier points must have created == 0 (no data seeded there).
    for point in result.points[:-1]:
        assert point.created == 0, f"Expected 0 created on {point.week_start}, got {point.created}"


async def test_ticket_flow_created_bucket_matches_week(session: AsyncSession) -> None:
    """Tickets created in different weeks land in the correct week buckets."""
    from app.modules.analytics.infrastructure.repositories import SqlAnalyticsRepository

    # Insert 2 tickets last week, 3 tickets this week.
    for _ in range(2):
        await _insert_ticket(session, created_at=_monday(-1))
    for _ in range(3):
        await _insert_ticket(session, created_at=_monday(0))

    repo = SqlAnalyticsRepository(session)
    result = await repo.ticket_flow(weeks=4)
    assert len(result.points) == 4

    # Last week is result.points[-2], current week is result.points[-1].
    last_week = result.points[-2]
    this_week = result.points[-1]

    assert last_week.created >= 2, f"Expected >= 2 created last week, got {last_week.created}"
    assert this_week.created >= 3, f"Expected >= 3 created this week, got {this_week.created}"


async def test_ticket_flow_closed_bucket(session: AsyncSession) -> None:
    """A ticket with closed_at in a specific week appears in that week's closed count."""
    from app.modules.analytics.infrastructure.repositories import SqlAnalyticsRepository

    # Create and close a ticket last week.
    closed_ts = _monday(-1) + datetime.timedelta(hours=2)
    await _insert_ticket(
        session,
        status="closed",
        created_at=_monday(-1),
        closed_at=closed_ts,
    )
    # Create a ticket this week, not closed.
    await _insert_ticket(session, created_at=_monday(0))

    repo = SqlAnalyticsRepository(session)
    result = await repo.ticket_flow(weeks=4)

    last_week_point = result.points[-2]
    this_week_point = result.points[-1]

    assert last_week_point.closed >= 1, (
        f"Expected >= 1 closed last week, got {last_week_point.closed}"
    )
    assert this_week_point.closed == 0, f"Expected 0 closed this week, got {this_week_point.closed}"


async def test_ticket_flow_rejected_not_counted_as_closed(session: AsyncSession) -> None:
    """A rejected ticket has closed_at set (both terminal statuses do) but must
    NOT be counted in the 'closed' series — only genuinely closed tickets are."""
    from app.modules.analytics.infrastructure.repositories import SqlAnalyticsRepository

    # The domain sets closed_at for BOTH closed and rejected terminal statuses.
    rejected_ts = _monday(-1) + datetime.timedelta(hours=3)
    await _insert_ticket(
        session,
        status="rejected",
        created_at=_monday(-1),
        closed_at=rejected_ts,
    )

    repo = SqlAnalyticsRepository(session)
    result = await repo.ticket_flow(weeks=4)

    total_closed = sum(p.closed for p in result.points)
    assert total_closed == 0, f"Rejected ticket leaked into the closed series (got {total_closed})"


async def test_ticket_flow_tickets_outside_window_not_counted(session: AsyncSession) -> None:
    """A ticket created 10 weeks ago must not appear in a 4-week window."""
    from app.modules.analytics.infrastructure.repositories import SqlAnalyticsRepository

    await _insert_ticket(session, created_at=_monday(-10))

    repo = SqlAnalyticsRepository(session)
    result = await repo.ticket_flow(weeks=4)

    total_created = sum(p.created for p in result.points)
    assert total_created == 0, (
        f"Expected 0 created in 4-week window, got {total_created} "
        "(ticket from 10 weeks ago leaked in)"
    )


async def test_ticket_flow_point_has_date_type(session: AsyncSession) -> None:
    """Each FlowPoint.week_start must be a datetime.date instance."""
    from app.modules.analytics.infrastructure.repositories import SqlAnalyticsRepository

    repo = SqlAnalyticsRepository(session)
    result = await repo.ticket_flow(weeks=2)
    for point in result.points:
        assert isinstance(point.week_start, datetime.date), (
            f"week_start is {type(point.week_start)}, expected datetime.date"
        )


# ---------------------------------------------------------------------------
# ticket_activity tests
# ---------------------------------------------------------------------------


async def test_ticket_activity_empty_returns_empty_list(session: AsyncSession) -> None:
    """With no audit_entries, ticket_activity returns []."""
    from app.modules.analytics.infrastructure.repositories import SqlAnalyticsRepository

    repo = SqlAnalyticsRepository(session)
    result = await repo.ticket_activity(limit=10)
    assert result == []


async def test_ticket_activity_filters_non_ticket_entries(session: AsyncSession) -> None:
    """Only rows with entity_type='ticket' must be returned."""
    from app.modules.analytics.infrastructure.repositories import SqlAnalyticsRepository

    # Insert non-ticket entries.
    for et in ("department", "template", "task"):
        await _insert_audit_entry(session, entity_type=et, action="updated")

    # Insert one ticket entry.
    ticket_id = await _insert_ticket(session, title="Filtered ticket")
    await _insert_audit_entry(session, entity_type="ticket", entity_id=ticket_id, action="created")

    repo = SqlAnalyticsRepository(session)
    result = await repo.ticket_activity(limit=50)

    assert len(result) == 1
    assert result[0].action == "created"


async def test_ticket_activity_most_recent_first(session: AsyncSession) -> None:
    """Results must be ordered by occurred_at DESC."""
    from app.modules.analytics.infrastructure.repositories import SqlAnalyticsRepository

    base = _monday(0)
    for i, delta in enumerate([0, 1, 3]):
        await _insert_audit_entry(
            session,
            entity_type="ticket",
            action=f"action_{i}",
            occurred_at=base + datetime.timedelta(hours=delta),
        )

    repo = SqlAnalyticsRepository(session)
    result = await repo.ticket_activity(limit=10)

    occurred = [r.occurred_at for r in result]
    assert occurred == sorted(occurred, reverse=True), (
        "ticket_activity results are not in descending occurred_at order"
    )


async def test_ticket_activity_respects_limit(session: AsyncSession) -> None:
    """Result list must not exceed the requested limit."""
    from app.modules.analytics.infrastructure.repositories import SqlAnalyticsRepository

    base = _monday(0)
    for i in range(7):
        await _insert_audit_entry(
            session,
            entity_type="ticket",
            action="created",
            occurred_at=base + datetime.timedelta(hours=i),
        )

    repo = SqlAnalyticsRepository(session)
    result = await repo.ticket_activity(limit=3)
    assert len(result) == 3


async def test_ticket_activity_ticket_title_joined(session: AsyncSession) -> None:
    """ticket_title must come from the tickets table when the row exists."""
    from app.modules.analytics.infrastructure.repositories import SqlAnalyticsRepository

    tid = await _insert_ticket(session, title="My Feature Request")
    await _insert_audit_entry(session, entity_type="ticket", entity_id=tid, action="created")

    repo = SqlAnalyticsRepository(session)
    result = await repo.ticket_activity(limit=5)

    matching = [r for r in result if r.entity_id == tid]
    assert len(matching) == 1
    assert matching[0].ticket_title == "My Feature Request"


async def test_ticket_activity_ticket_title_none_when_no_matching_ticket(
    session: AsyncSession,
) -> None:
    """ticket_title must be None when no tickets row exists for entity_id."""
    from app.modules.analytics.infrastructure.repositories import SqlAnalyticsRepository

    orphan_id = uuid.uuid4()  # no ticket row for this id
    await _insert_audit_entry(session, entity_type="ticket", entity_id=orphan_id, action="created")

    repo = SqlAnalyticsRepository(session)
    result = await repo.ticket_activity(limit=5)

    matching = [r for r in result if r.entity_id == orphan_id]
    assert len(matching) == 1
    assert matching[0].ticket_title is None


async def test_ticket_activity_status_from_after_field(session: AsyncSession) -> None:
    """status must equal after->>'status' when the field is present."""
    from app.modules.analytics.infrastructure.repositories import SqlAnalyticsRepository

    await _insert_audit_entry(
        session,
        entity_type="ticket",
        action="status_changed",
        after={"status": "in_progress", "title": "ignored"},
    )

    repo = SqlAnalyticsRepository(session)
    result = await repo.ticket_activity(limit=5)

    matching = [r for r in result if r.action == "status_changed"]
    assert len(matching) >= 1
    assert matching[0].status == "in_progress"


async def test_ticket_activity_status_none_when_after_has_no_status(
    session: AsyncSession,
) -> None:
    """status must be None when after is NULL or lacks a 'status' key."""
    from app.modules.analytics.infrastructure.repositories import SqlAnalyticsRepository

    # Entry with after=None.
    await _insert_audit_entry(session, entity_type="ticket", action="viewed", after=None)
    # Entry with after that has no 'status' key.
    await _insert_audit_entry(
        session, entity_type="ticket", action="commented", after={"note": "ok"}
    )

    repo = SqlAnalyticsRepository(session)
    result = await repo.ticket_activity(limit=10)

    for item in result:
        if item.action in ("viewed", "commented"):
            assert item.status is None, (
                f"Expected status=None for action={item.action!r}, got {item.status!r}"
            )


async def test_ticket_activity_limit_one_returns_most_recent(session: AsyncSession) -> None:
    """limit=1 must return the single most recent audit entry."""
    from app.modules.analytics.infrastructure.repositories import SqlAnalyticsRepository

    base = _monday(0)
    for i, action in enumerate(["old_event", "newer_event", "newest_event"]):
        await _insert_audit_entry(
            session,
            entity_type="ticket",
            action=action,
            occurred_at=base + datetime.timedelta(hours=i),
        )

    repo = SqlAnalyticsRepository(session)
    result = await repo.ticket_activity(limit=1)

    assert len(result) == 1
    assert result[0].action == "newest_event"


async def test_ticket_activity_actor_field_populated(session: AsyncSession) -> None:
    """ActivityItem.actor must match the inserted actor value."""
    from app.modules.analytics.infrastructure.repositories import SqlAnalyticsRepository

    expected_actor = "user-sub-abc123"
    await _insert_audit_entry(
        session,
        entity_type="ticket",
        action="created",
        actor=expected_actor,
    )

    repo = SqlAnalyticsRepository(session)
    result = await repo.ticket_activity(limit=5)

    assert any(r.actor == expected_actor for r in result), (
        f"Expected actor {expected_actor!r} in results, got {[r.actor for r in result]}"
    )


async def test_ticket_activity_entry_id_is_the_audit_row_id(session: AsyncSession) -> None:
    """entry_id must be the audit_entries row id (stable React key), distinct
    from entity_id (the ticket id)."""
    from app.modules.analytics.infrastructure.repositories import SqlAnalyticsRepository

    tid = await _insert_ticket(session, title="Keyed ticket")
    eid = await _insert_audit_entry(session, entity_type="ticket", entity_id=tid, action="created")

    repo = SqlAnalyticsRepository(session)
    result = await repo.ticket_activity(limit=5)

    matching = [r for r in result if r.entry_id == eid]
    assert len(matching) == 1
    assert matching[0].entity_id == tid
    assert matching[0].entry_id != matching[0].entity_id
