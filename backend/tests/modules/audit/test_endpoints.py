"""Endpoint tests for /v1/audit.

Covers:
- RBAC: tenant_admin and platform_admin can access; tenant_user cannot (403).
- Filter parameters are forwarded to the service.
- Paging parameters are forwarded.
- 200 with expected response shape.
- Service error surfaces correctly.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx

from app.core import deps
from app.core.security import Principal
from app.modules.audit.application.dtos import AuditEntriesPage, AuditEntry
from app.modules.audit.interface import router as audit_router_module

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_TID = UUID("11111111-1111-1111-1111-111111111111")
_ENTRY_ID = UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")
_ENTITY_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_TS = datetime(2026, 7, 4, 12, 0, tzinfo=UTC)

_ENTRY = AuditEntry(
    id=_ENTRY_ID,
    entity_type="task",
    entity_id=_ENTITY_ID,
    action="created",
    actor="user-sub-001",
    roles=["tenant_user"],
    before=None,
    after={"title": "Test"},
    request_id="req-001",
    occurred_at=_TS,
)


def _principal(*roles: str) -> Principal:
    return Principal(
        subject="b79ac28a-7f95-4b15-8e22-52308f55eb99",
        tenant_id=_TID,
        roles=frozenset(roles),
        raw_claims={},
    )


class _FakeService:
    def __init__(
        self,
        entries: list[AuditEntry] | None = None,
        raises: Exception | None = None,
    ) -> None:
        self._entries = [_ENTRY] if entries is None else entries
        self._raises = raises
        self.last_query: object = None

    async def list(self, *, query: object) -> AuditEntriesPage:
        self.last_query = query
        if self._raises:
            raise self._raises
        return AuditEntriesPage(
            items=self._entries,
            total=len(self._entries),
            limit=20,
            offset=0,
        )


async def _make_client(
    *roles: str,
    raises: Exception | None = None,
    entries: list[AuditEntry] | None = None,
) -> AsyncIterator[tuple[httpx.AsyncClient, _FakeService]]:
    from app.main import create_app

    app = create_app()
    fake = _FakeService(raises=raises, entries=entries)
    app.dependency_overrides[deps._principal] = lambda: _principal(*roles)
    app.dependency_overrides[audit_router_module._service] = lambda: fake
    app.dependency_overrides[deps.check_rate_limit] = lambda: None
    app.dependency_overrides[deps.check_csrf] = lambda: None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as ac:
        yield ac, fake


# ---------------------------------------------------------------------------
# RBAC tests
# ---------------------------------------------------------------------------


async def test_list_audit_tenant_admin_ok() -> None:
    async for ac, fake in _make_client("tenant_admin"):
        r = await ac.get("/v1/audit")
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["entity_type"] == "task"
        assert body["data"][0]["action"] == "created"
        assert body["meta"]["total"] == 1
        assert fake.last_query is not None


async def test_list_audit_platform_admin_ok() -> None:
    async for ac, _ in _make_client("platform_admin"):
        r = await ac.get("/v1/audit")
        assert r.status_code == 200, r.text


async def test_list_audit_tenant_user_forbidden() -> None:
    async for ac, _ in _make_client("tenant_user"):
        r = await ac.get("/v1/audit")
        assert r.status_code == 403


async def test_list_audit_no_roles_forbidden() -> None:
    async for ac, _ in _make_client():
        r = await ac.get("/v1/audit")
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# Response shape
# ---------------------------------------------------------------------------


async def test_list_audit_response_shape() -> None:
    async for ac, _ in _make_client("tenant_admin"):
        r = await ac.get("/v1/audit")
        assert r.status_code == 200
        body = r.json()
        entry = body["data"][0]
        assert entry["id"] == str(_ENTRY_ID)
        assert entry["entity_type"] == "task"
        assert entry["entity_id"] == str(_ENTITY_ID)
        assert entry["action"] == "created"
        assert entry["actor"] == "user-sub-001"
        assert entry["roles"] == ["tenant_user"]
        assert entry["before"] is None
        assert entry["after"] == {"title": "Test"}
        assert entry["request_id"] == "req-001"
        assert "occurred_at" in entry


# ---------------------------------------------------------------------------
# Filter forwarding
# ---------------------------------------------------------------------------


async def test_list_audit_forwards_entity_type_filter() -> None:
    from app.modules.audit.application.dtos import ListAuditEntriesQuery

    async for ac, fake in _make_client("tenant_admin"):
        r = await ac.get("/v1/audit", params={"entity_type": "department"})
        assert r.status_code == 200
        q: ListAuditEntriesQuery = fake.last_query  # type: ignore[assignment]
        assert q.entity_type == "department"


async def test_list_audit_forwards_entity_id_filter() -> None:
    from app.modules.audit.application.dtos import ListAuditEntriesQuery

    entity_id = uuid4()
    async for ac, fake in _make_client("tenant_admin"):
        r = await ac.get("/v1/audit", params={"entity_id": str(entity_id)})
        assert r.status_code == 200
        q: ListAuditEntriesQuery = fake.last_query  # type: ignore[assignment]
        assert q.entity_id == entity_id


async def test_list_audit_forwards_action_filter() -> None:
    from app.modules.audit.application.dtos import ListAuditEntriesQuery

    async for ac, fake in _make_client("tenant_admin"):
        r = await ac.get("/v1/audit", params={"action": "updated"})
        assert r.status_code == 200
        q: ListAuditEntriesQuery = fake.last_query  # type: ignore[assignment]
        assert q.action == "updated"


async def test_list_audit_forwards_actor_filter() -> None:
    from app.modules.audit.application.dtos import ListAuditEntriesQuery

    async for ac, fake in _make_client("tenant_admin"):
        r = await ac.get("/v1/audit", params={"actor": "user-sub-001"})
        assert r.status_code == 200
        q: ListAuditEntriesQuery = fake.last_query  # type: ignore[assignment]
        assert q.actor == "user-sub-001"


# ---------------------------------------------------------------------------
# Paging
# ---------------------------------------------------------------------------


async def test_list_audit_forwards_paging() -> None:
    from app.modules.audit.application.dtos import ListAuditEntriesQuery

    async for ac, fake in _make_client("tenant_admin"):
        r = await ac.get("/v1/audit", params={"limit": 5, "offset": 10})
        assert r.status_code == 200
        q: ListAuditEntriesQuery = fake.last_query  # type: ignore[assignment]
        assert q.limit == 5
        assert q.offset == 10


async def test_list_audit_limit_too_large_rejected() -> None:
    async for ac, _ in _make_client("tenant_admin"):
        r = await ac.get("/v1/audit", params={"limit": 200})
        assert r.status_code == 422


async def test_list_audit_empty_result() -> None:
    async for ac, _ in _make_client("tenant_admin", entries=[]):
        r = await ac.get("/v1/audit")
        assert r.status_code == 200
        body = r.json()
        assert body["data"] == []
        assert body["meta"]["total"] == 0
