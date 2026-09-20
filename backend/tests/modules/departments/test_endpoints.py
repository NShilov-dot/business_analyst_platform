"""Endpoint tests for /api/v1/departments.

The DepartmentService is dependency-overridden, so no real DB/Redis is touched.
Tests cover: routing, RBAC (non-admin forbidden / admin allowed), request
validation, and response shape.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx

from app.core import deps
from app.core.security import Principal
from app.modules.departments.application.dtos import (
    Department,
    DepartmentMembership,
    DepartmentsPage,
)
from app.modules.departments.application.errors import (
    DepartmentNameConflictError,
    DepartmentNotFoundError,
)
from app.modules.departments.interface import router as dept_router_module

_TID = UUID("11111111-1111-1111-1111-111111111111")
_DEPT_ID = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
_MEMBER_ID = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
_TS = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)

_DEPT = Department(
    id=_DEPT_ID,
    name="Engineering",
    description=None,
    is_active=True,
    created_at=_TS,
    updated_at=_TS,
)

_MEMBERSHIP = DepartmentMembership(
    id=_MEMBER_ID,
    department_id=_DEPT_ID,
    subject="user-sub-001",
)


def _principal(*roles: str) -> Principal:
    return Principal(
        subject="b79ac28a-7f95-4b15-8e22-52308f55eb99",
        tenant_id=_TID,
        roles=frozenset(roles),
        raw_claims={},
    )


class _FakeService:
    """Minimal fake that records calls and returns canned responses."""

    def __init__(
        self,
        dept: Department | None = None,
        membership: DepartmentMembership | None = None,
        raises: Exception | None = None,
    ) -> None:
        self._dept = dept or _DEPT
        self._membership = membership or _MEMBERSHIP
        self._raises = raises
        self.calls: list[str] = []

    def _maybe_raise(self) -> None:
        if self._raises is not None:
            raise self._raises

    async def create(self, *, command: object, actor: str = "") -> Department:
        self.calls.append("create")
        self._maybe_raise()
        return self._dept

    async def get(self, *, dept_id: UUID) -> Department:
        self.calls.append("get")
        self._maybe_raise()
        return self._dept

    async def list(self, *, query: object) -> DepartmentsPage:
        self.calls.append("list")
        self._maybe_raise()
        return DepartmentsPage(items=[self._dept], total=1, limit=20, offset=0)

    async def update(self, *, dept_id: UUID, command: object, actor: str = "") -> Department:
        self.calls.append("update")
        self._maybe_raise()
        return self._dept

    async def add_member(
        self, *, dept_id: UUID, subject: str, actor: str = ""
    ) -> DepartmentMembership:
        self.calls.append("add_member")
        self._maybe_raise()
        return self._membership

    async def remove_member(self, *, dept_id: UUID, subject: str, actor: str = "") -> None:
        self.calls.append("remove_member")
        self._maybe_raise()


async def _make_client(
    *roles: str,
    raises: Exception | None = None,
    dept: Department | None = None,
) -> AsyncIterator[tuple[httpx.AsyncClient, _FakeService]]:
    from app.main import create_app

    app = create_app()
    fake = _FakeService(raises=raises, dept=dept)
    app.dependency_overrides[deps._principal] = lambda: _principal(*roles)
    app.dependency_overrides[dept_router_module._service] = lambda: fake
    app.dependency_overrides[deps.check_rate_limit] = lambda: None
    app.dependency_overrides[deps.check_csrf] = lambda: None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as ac:
        yield ac, fake


# ---------------------------------------------------------------------------
# GET /api/v1/departments — any authenticated member
# ---------------------------------------------------------------------------


async def test_list_departments_authenticated() -> None:
    async for ac, fake in _make_client("tenant_user"):
        r = await ac.get("/api/v1/departments")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["data"][0]["name"] == "Engineering"
        assert body["meta"]["total"] == 1
        assert fake.calls == ["list"]


async def test_list_departments_pagination_params() -> None:
    async for ac, _ in _make_client("tenant_user"):
        r = await ac.get("/api/v1/departments", params={"limit": 5, "offset": 10})
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/v1/departments/{id} — any authenticated member
# ---------------------------------------------------------------------------


async def test_get_department_authenticated() -> None:
    async for ac, fake in _make_client("tenant_user"):
        r = await ac.get(f"/api/v1/departments/{_DEPT_ID}")
        assert r.status_code == 200, r.text
        assert r.json()["data"]["id"] == str(_DEPT_ID)
        assert fake.calls == ["get"]


async def test_get_department_not_found() -> None:
    async for ac, _ in _make_client("tenant_user", raises=DepartmentNotFoundError("not found")):
        r = await ac.get(f"/api/v1/departments/{uuid4()}")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/v1/departments — tenant_admin only
# ---------------------------------------------------------------------------


async def test_create_department_admin() -> None:
    async for ac, fake in _make_client("tenant_admin"):
        r = await ac.post("/api/v1/departments", json={"name": "Engineering"})
        assert r.status_code == 201, r.text
        assert r.json()["data"]["name"] == "Engineering"
        assert fake.calls == ["create"]


async def test_create_department_platform_admin_passes() -> None:
    """platform_admin must also be allowed (matches _ADMIN_ROLES in the router)."""
    async for ac, fake in _make_client("platform_admin"):
        r = await ac.post("/api/v1/departments", json={"name": "Engineering"})
        assert r.status_code == 201, r.text
        assert fake.calls == ["create"]


async def test_create_department_tenant_user_forbidden() -> None:
    async for ac, fake in _make_client("tenant_user"):
        r = await ac.post("/api/v1/departments", json={"name": "Engineering"})
        assert r.status_code == 403, r.text
        assert fake.calls == []


async def test_create_department_unauthenticated_forbidden() -> None:
    async for ac, fake in _make_client():  # no roles
        r = await ac.post("/api/v1/departments", json={"name": "Engineering"})
        assert r.status_code == 403
        assert fake.calls == []


async def test_create_department_conflict() -> None:
    err = DepartmentNameConflictError("already exists")
    async for ac, _ in _make_client("tenant_admin", raises=err):
        r = await ac.post("/api/v1/departments", json={"name": "Engineering"})
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "DEPARTMENT_NAME_CONFLICT"


async def test_create_department_invalid_body() -> None:
    async for ac, _ in _make_client("tenant_admin"):
        # name is required
        r = await ac.post("/api/v1/departments", json={})
        assert r.status_code == 422


async def test_create_department_blank_name_rejected() -> None:
    """Whitespace-only name must be rejected at the schema layer (422, not 409)."""
    async for ac, _ in _make_client("tenant_admin"):
        r = await ac.post("/api/v1/departments", json={"name": "   "})
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# PATCH /api/v1/departments/{id} — tenant_admin only
# ---------------------------------------------------------------------------


async def test_update_department_admin() -> None:
    async for ac, fake in _make_client("tenant_admin"):
        r = await ac.patch(f"/api/v1/departments/{_DEPT_ID}", json={"is_active": False})
        assert r.status_code == 200, r.text
        assert fake.calls == ["update"]


async def test_update_department_non_admin_forbidden() -> None:
    async for ac, fake in _make_client("tenant_user"):
        r = await ac.patch(f"/api/v1/departments/{_DEPT_ID}", json={"is_active": False})
        assert r.status_code == 403
        assert fake.calls == []


async def test_update_department_not_found() -> None:
    async for ac, _ in _make_client("tenant_admin", raises=DepartmentNotFoundError("not found")):
        r = await ac.patch(f"/api/v1/departments/{uuid4()}", json={"name": "x"})
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/v1/departments/{id}/members — tenant_admin only
# ---------------------------------------------------------------------------


async def test_add_member_admin() -> None:
    """add_member is idempotent — the endpoint always returns 200."""
    async for ac, fake in _make_client("tenant_admin"):
        r = await ac.post(
            f"/api/v1/departments/{_DEPT_ID}/members",
            json={"subject": "user-sub-001"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["data"]["subject"] == "user-sub-001"
        assert fake.calls == ["add_member"]


async def test_add_member_non_admin_forbidden() -> None:
    async for ac, fake in _make_client("tenant_user"):
        r = await ac.post(
            f"/api/v1/departments/{_DEPT_ID}/members",
            json={"subject": "user-sub-001"},
        )
        assert r.status_code == 403
        assert fake.calls == []


async def test_add_member_dept_not_found() -> None:
    async for ac, _ in _make_client("tenant_admin", raises=DepartmentNotFoundError("not found")):
        r = await ac.post(
            f"/api/v1/departments/{uuid4()}/members",
            json={"subject": "user-sub-001"},
        )
        assert r.status_code == 404


async def test_add_member_invalid_body() -> None:
    async for ac, _ in _make_client("tenant_admin"):
        r = await ac.post(f"/api/v1/departments/{_DEPT_ID}/members", json={})
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /api/v1/departments/{id}/members/{subject} — tenant_admin only
# ---------------------------------------------------------------------------


async def test_remove_member_admin() -> None:
    async for ac, fake in _make_client("tenant_admin"):
        r = await ac.delete(f"/api/v1/departments/{_DEPT_ID}/members/user-sub-001")
        assert r.status_code == 204, r.text
        assert fake.calls == ["remove_member"]


async def test_remove_member_non_admin_forbidden() -> None:
    async for ac, fake in _make_client("tenant_user"):
        r = await ac.delete(f"/api/v1/departments/{_DEPT_ID}/members/user-sub-001")
        assert r.status_code == 403
        assert fake.calls == []


async def test_remove_member_dept_not_found() -> None:
    async for ac, _ in _make_client("tenant_admin", raises=DepartmentNotFoundError("not found")):
        r = await ac.delete(f"/api/v1/departments/{uuid4()}/members/user-sub-001")
        assert r.status_code == 404
