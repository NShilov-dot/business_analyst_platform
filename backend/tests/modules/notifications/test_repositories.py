"""Integration tests for SqlNotificationsRepository.

Uses a real Postgres container (via testcontainers) with the full tenant
schema applied through Alembic migrations, mirroring the analytics tests.

These assertions ARE the security surface of the feature — the feed is derived
from audit_entries, so "who sees which row" is decided entirely by the SQL
predicate under test:
- involvement via authorship, active assignment, and prior action;
- non-involvement is not shown;
- own actions are never notified back to the actor;
- the `ba` role sees the triage/acceptance queue and nothing more;
- dismissing one event clears exactly it and leaves older unread ones unread;
- unread_count is the true total, unaffected by `limit` or `include_read`.
"""

from __future__ import annotations

import argparse
import datetime
import os
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Env vars must exist before any app import (alembic/env.py calls get_settings()).
os.environ.setdefault("APP_ENV", "local")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("KEYCLOAK_ISSUER", "http://localhost:8080/realms/bap")
os.environ.setdefault("KEYCLOAK_AUDIENCE", "bap-backend")

_SCHEMA = "tenant_notifications_test"
_UTC = datetime.UTC


@pytest.fixture(scope="session")
def _pg_url() -> str:  # type: ignore[return]
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine") as pg:
        sync_url: str = pg.get_connection_url()
        async_url = sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
        os.environ["DATABASE_URL"] = async_url

        import importlib

        import app.config as _cfg

        importlib.reload(_cfg)

        from alembic import command as alembic_cmd
        from alembic.config import Config

        pub_cfg = Config("alembic.ini")
        pub_cfg.cmd_opts = argparse.Namespace(x=["scope=public"])
        alembic_cmd.upgrade(pub_cfg, "public@head")

        tenant_cfg = Config("alembic.ini")
        tenant_cfg.cmd_opts = argparse.Namespace(x=["scope=tenant", f"schema={_SCHEMA}"])
        alembic_cmd.upgrade(tenant_cfg, "tenant@head")

        yield async_url


@pytest.fixture
async def session(_pg_url: str) -> AsyncIterator[AsyncSession]:
    """AsyncSession scoped to the tenant schema; all writes rolled back."""
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


def _ts(minutes_ago: int = 0) -> datetime.datetime:
    return datetime.datetime.now(_UTC) - datetime.timedelta(minutes=minutes_ago)


async def _insert_ticket(
    session: AsyncSession, *, author: str, title: str = "T", status: str = "created"
) -> uuid.UUID:
    tid = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO tickets (id, title, status, author_id, created_at, updated_at) "
            "VALUES (:id, :title, :status, :author_id, :ts, :ts)"
        ),
        {"id": tid, "title": title, "status": status, "author_id": author, "ts": _ts()},
    )
    await session.flush()
    return tid


async def _insert_event(
    session: AsyncSession,
    *,
    ticket_id: uuid.UUID,
    action: str,
    actor: str,
    occurred_at: datetime.datetime | None = None,
    entity_type: str = "ticket",
) -> uuid.UUID:
    eid = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO audit_entries "
            "(id, entity_type, entity_id, action, actor, occurred_at) "
            "VALUES (:id, :et, :eid, :action, :actor, :ts)"
        ),
        {
            "id": eid,
            "et": entity_type,
            "eid": ticket_id,
            "action": action,
            "actor": actor,
            "ts": occurred_at or _ts(),
        },
    )
    await session.flush()
    return eid


async def _assign(
    session: AsyncSession,
    *,
    ticket_id: uuid.UUID,
    subject: str,
    role: str = "executor",
    active: bool = True,
) -> None:
    await session.execute(
        text(
            "INSERT INTO ticket_assignments "
            "(id, ticket_id, role, subject, assigned_by, assigned_at, unassigned_at) "
            "VALUES (:id, :tid, :role, :subject, :by, :ts, :off)"
        ),
        {
            "id": uuid.uuid4(),
            "tid": ticket_id,
            "role": role,
            "subject": subject,
            "by": "someone-else",
            "ts": _ts(10),
            "off": None if active else _ts(1),
        },
    )
    await session.flush()


def _repo(session: AsyncSession):  # type: ignore[no-untyped-def]
    from app.modules.notifications.infrastructure.repositories import (
        SqlNotificationsRepository,
    )

    return SqlNotificationsRepository(session)


# ---------------------------------------------------------------------------
# Who sees what
# ---------------------------------------------------------------------------


async def test_author_sees_events_on_own_ticket(session: AsyncSession) -> None:
    me = str(uuid.uuid4())
    ticket = await _insert_ticket(session, author=me, title="Мой тикет")
    await _insert_event(session, ticket_id=ticket, action="triage_accepted", actor="ba-1")

    items = await _repo(session).feed(include_read=False, subject=me, is_ba=False, limit=20)

    assert [i.action for i in items] == ["triage_accepted"]
    assert items[0].ticket_id == ticket
    assert items[0].ticket_title == "Мой тикет"


async def test_uninvolved_user_sees_nothing(session: AsyncSession) -> None:
    stranger = str(uuid.uuid4())
    ticket = await _insert_ticket(session, author=str(uuid.uuid4()))
    await _insert_event(session, ticket_id=ticket, action="work_started", actor="exec-1")

    items = await _repo(session).feed(include_read=False, subject=stranger, is_ba=False, limit=20)

    assert items == []


async def test_own_actions_are_not_notified_back(session: AsyncSession) -> None:
    me = str(uuid.uuid4())
    ticket = await _insert_ticket(session, author=me)
    await _insert_event(session, ticket_id=ticket, action="submitted", actor=me)
    await _insert_event(session, ticket_id=ticket, action="rejected", actor="ba-1")

    items = await _repo(session).feed(include_read=False, subject=me, is_ba=False, limit=20)

    assert [i.action for i in items] == ["rejected"]


async def test_active_assignee_sees_events_non_assignee_does_not(
    session: AsyncSession,
) -> None:
    executor = str(uuid.uuid4())
    former = str(uuid.uuid4())
    ticket = await _insert_ticket(session, author=str(uuid.uuid4()))
    await _assign(session, ticket_id=ticket, subject=executor, active=True)
    await _assign(session, ticket_id=ticket, subject=former, role="business_owner", active=False)
    await _insert_event(session, ticket_id=ticket, action="returned_to_work", actor="ba-1")

    repo = _repo(session)

    assert len(await repo.feed(include_read=False, subject=executor, is_ba=False, limit=20)) == 1
    assert await repo.feed(include_read=False, subject=former, is_ba=False, limit=20) == []


async def test_prior_actor_keeps_following_the_ticket(session: AsyncSession) -> None:
    """A BA who triaged a ticket keeps getting its updates without an assignment."""
    ba = str(uuid.uuid4())
    ticket = await _insert_ticket(session, author=str(uuid.uuid4()))
    await _insert_event(session, ticket_id=ticket, action="triage_accepted", actor=ba)
    await _insert_event(session, ticket_id=ticket, action="work_finished", actor="exec-1")

    items = await _repo(session).feed(include_read=False, subject=ba, is_ba=False, limit=20)

    assert [i.action for i in items] == ["work_finished"]


async def test_ba_sees_the_queue_but_only_queue_actions(session: AsyncSession) -> None:
    ba = str(uuid.uuid4())
    untouched = await _insert_ticket(session, author=str(uuid.uuid4()))
    await _insert_event(session, ticket_id=untouched, action="submitted", actor="requester-1")
    await _insert_event(session, ticket_id=untouched, action="acceptance_requested", actor="exec-1")
    await _insert_event(session, ticket_id=untouched, action="updated", actor="requester-1")

    repo = _repo(session)

    assert await repo.feed(include_read=False, subject=ba, is_ba=False, limit=20) == []
    assert sorted(
        i.action for i in await repo.feed(include_read=False, subject=ba, is_ba=True, limit=20)
    ) == [
        "acceptance_requested",
        "submitted",
    ]


async def test_non_ticket_audit_rows_are_excluded(session: AsyncSession) -> None:
    me = str(uuid.uuid4())
    ticket = await _insert_ticket(session, author=me)
    await _insert_event(
        session, ticket_id=ticket, action="created", actor="admin", entity_type="department"
    )

    assert await _repo(session).feed(include_read=False, subject=me, is_ba=False, limit=20) == []


async def test_feed_is_newest_first_and_limited(session: AsyncSession) -> None:
    me = str(uuid.uuid4())
    ticket = await _insert_ticket(session, author=me)
    for n, action in enumerate(["submitted", "triage_accepted", "work_started"]):
        await _insert_event(
            session, ticket_id=ticket, action=action, actor="ba-1", occurred_at=_ts(10 - n)
        )

    items = await _repo(session).feed(include_read=False, subject=me, is_ba=False, limit=2)

    assert [i.action for i in items] == ["work_started", "triage_accepted"]


# ---------------------------------------------------------------------------
# Unread count + watermark
# ---------------------------------------------------------------------------


async def test_dismissing_one_event_leaves_older_ones_unread(
    session: AsyncSession,
) -> None:
    """The point of per-item marks: clearing the newest must not clear the rest."""
    me = str(uuid.uuid4())
    ticket = await _insert_ticket(session, author=me)
    old = await _insert_event(
        session, ticket_id=ticket, action="submitted", actor="ba-1", occurred_at=_ts(30)
    )
    new = await _insert_event(
        session, ticket_id=ticket, action="work_started", actor="ba-1", occurred_at=_ts(10)
    )
    repo = _repo(session)

    assert await repo.unread_count(subject=me, is_ba=False) == 2

    await repo.mark_read(subject=me, entry_ids=[new])

    assert await repo.unread_count(subject=me, is_ba=False) == 1
    unread = await repo.feed(include_read=False, subject=me, is_ba=False, limit=20)
    assert [i.id for i in unread] == [old]


async def test_include_read_returns_dismissed_events_flagged(
    session: AsyncSession,
) -> None:
    me = str(uuid.uuid4())
    ticket = await _insert_ticket(session, author=me)
    seen = await _insert_event(
        session, ticket_id=ticket, action="submitted", actor="ba-1", occurred_at=_ts(30)
    )
    await _insert_event(
        session, ticket_id=ticket, action="work_started", actor="ba-1", occurred_at=_ts(10)
    )
    repo = _repo(session)
    await repo.mark_read(subject=me, entry_ids=[seen])

    all_items = await repo.feed(include_read=True, subject=me, is_ba=False, limit=20)

    assert len(all_items) == 2
    assert {i.id: i.is_read for i in all_items}[seen] is True
    assert all(i.is_read is False for i in all_items if i.id != seen)


async def test_mark_read_is_idempotent_and_scoped_to_the_subject(
    session: AsyncSession,
) -> None:
    me = str(uuid.uuid4())
    someone_else = str(uuid.uuid4())
    ticket = await _insert_ticket(session, author=me)
    await _insert_ticket(session, author=someone_else)
    event = await _insert_event(session, ticket_id=ticket, action="submitted", actor="ba-1")
    repo = _repo(session)

    await repo.mark_read(subject=me, entry_ids=[event])
    await repo.mark_read(subject=me, entry_ids=[event])  # replay must not raise

    assert await repo.unread_count(subject=me, is_ba=False) == 0
    # Another user dismissing nothing of theirs is unaffected by my mark.
    assert await repo.unread_count(subject=someone_else, is_ba=False) == 0


async def test_unread_count_ignores_the_feed_limit(session: AsyncSession) -> None:
    """The badge is the true total, not the size of the rendered page."""
    me = str(uuid.uuid4())
    ticket = await _insert_ticket(session, author=me)
    for n in range(5):
        await _insert_event(
            session, ticket_id=ticket, action="updated", actor="ba-1", occurred_at=_ts(30 - n)
        )
    repo = _repo(session)

    assert len(await repo.feed(include_read=False, subject=me, is_ba=False, limit=2)) == 2
    assert await repo.unread_count(subject=me, is_ba=False) == 5


async def test_mark_read_with_no_ids_is_a_noop(session: AsyncSession) -> None:
    me = str(uuid.uuid4())
    ticket = await _insert_ticket(session, author=me)
    await _insert_event(session, ticket_id=ticket, action="submitted", actor="ba-1")
    repo = _repo(session)

    await repo.mark_read(subject=me, entry_ids=[])

    assert await repo.unread_count(subject=me, is_ba=False) == 1
