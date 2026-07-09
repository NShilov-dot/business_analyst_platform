"""Service-level tests for DepartmentService using an in-memory fake repository.

Covers:
- Create and rename (name uniqueness invariant)
- Deactivate (is_active flag)
- Duplicate-name conflict
- Membership add/remove idempotency
- Domain event emission on every mutating path
- NO event emission on no-op/error paths
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.modules.departments.application.dtos import (
    CreateDepartmentCommand,
    Department,
    DepartmentMembership,
    ListDepartmentsQuery,
    UpdateDepartmentCommand,
)
from app.modules.departments.application.errors import (
    DepartmentNameConflictError,
    DepartmentNotFoundError,
)
from app.modules.departments.application.services import DepartmentService

# ---------------------------------------------------------------------------
# Fake repository
# ---------------------------------------------------------------------------


class FakeRepo:
    def __init__(self) -> None:
        self._depts: dict[UUID, Department] = {}
        self._memberships: dict[UUID, DepartmentMembership] = {}

    async def add(self, dept: Department) -> None:
        for d in self._depts.values():
            if d.name == dept.name:
                raise DepartmentNameConflictError(
                    f"Department named '{dept.name}' already exists in this tenant"
                )
        self._depts[dept.id] = dept

    async def get_by_id(self, dept_id: UUID) -> Department | None:
        return self._depts.get(dept_id)

    async def get_by_name(self, name: str) -> Department | None:
        for d in self._depts.values():
            if d.name == name:
                return d
        return None

    async def list_departments(self, *, limit: int, offset: int) -> tuple[list[Department], int]:
        rows = sorted(self._depts.values(), key=lambda d: d.name)
        return rows[offset : offset + limit], len(rows)

    async def update(self, dept: Department) -> Department:
        self._depts[dept.id] = dept
        return dept

    async def add_member(self, membership: DepartmentMembership) -> None:
        self._memberships[membership.id] = membership

    async def get_membership(self, dept_id: UUID, subject: str) -> DepartmentMembership | None:
        for m in self._memberships.values():
            if m.department_id == dept_id and m.subject == subject:
                return m
        return None

    async def remove_member(self, membership_id: UUID) -> None:
        self._memberships.pop(membership_id, None)


# ---------------------------------------------------------------------------
# Fake event publisher
# ---------------------------------------------------------------------------


class FakePublisher:
    """Captures published events for assertion."""

    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def __call__(
        self,
        entity_type: str,
        entity_id: UUID,
        action: str,
        *,
        before: dict[str, object] | None = None,
        after: dict[str, object] | None = None,
    ) -> None:
        self.events.append(
            {
                "entity_type": entity_type,
                "entity_id": entity_id,
                "action": action,
                "before": before,
                "after": after,
            }
        )

    @property
    def last(self) -> dict[str, object]:
        assert self.events, "No events were published"
        return self.events[-1]

    def assert_emitted(self, action: str, entity_id: UUID | None = None) -> dict[str, object]:
        matching = [e for e in self.events if e["action"] == action]
        assert matching, (
            f"No event with action={action!r} was published. Got: {[e['action'] for e in self.events]}"
        )
        evt = matching[-1]
        if entity_id is not None:
            assert evt["entity_id"] == entity_id
        return evt

    def assert_not_emitted(self, action: str) -> None:
        actions = [e["action"] for e in self.events]
        assert action not in actions, f"Event {action!r} was unexpectedly published"

    def assert_count(self, n: int) -> None:
        assert len(self.events) == n, (
            f"Expected {n} events, got {len(self.events)}: {[e['action'] for e in self.events]}"
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_FIXED_TS = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)


@pytest.fixture
def repo() -> FakeRepo:
    return FakeRepo()


@pytest.fixture
def pub() -> FakePublisher:
    return FakePublisher()


@pytest.fixture
def service(repo: FakeRepo, pub: FakePublisher) -> DepartmentService:
    return DepartmentService(repo=repo, clock=lambda: _FIXED_TS, publisher=pub)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


async def test_create_persists(service: DepartmentService, repo: FakeRepo) -> None:
    dept = await service.create(command=CreateDepartmentCommand(name="Engineering"))
    assert dept.name == "Engineering"
    assert dept.is_active is True
    assert dept.created_at == _FIXED_TS
    assert await repo.get_by_id(dept.id) is not None


async def test_create_strips_whitespace(service: DepartmentService) -> None:
    dept = await service.create(command=CreateDepartmentCommand(name="  Sales  "))
    assert dept.name == "Sales"


async def test_create_duplicate_name_raises_conflict(service: DepartmentService) -> None:
    await service.create(command=CreateDepartmentCommand(name="HR"))
    with pytest.raises(DepartmentNameConflictError):
        await service.create(command=CreateDepartmentCommand(name="HR"))


async def test_create_with_description(service: DepartmentService) -> None:
    dept = await service.create(
        command=CreateDepartmentCommand(name="Product", description="Product team")
    )
    assert dept.description == "Product team"


async def test_create_emits_created_event(service: DepartmentService, pub: FakePublisher) -> None:
    dept = await service.create(command=CreateDepartmentCommand(name="Engineering"))
    evt = pub.assert_emitted("created", dept.id)
    assert evt["entity_type"] == "department"
    assert evt["before"] is None
    assert isinstance(evt["after"], dict)
    after = evt["after"]
    assert after["name"] == "Engineering"  # type: ignore[index]
    assert after["is_active"] is True  # type: ignore[index]


async def test_create_conflict_no_event(service: DepartmentService, pub: FakePublisher) -> None:
    await service.create(command=CreateDepartmentCommand(name="HR"))
    pub.events.clear()  # reset after first successful create
    with pytest.raises(DepartmentNameConflictError):
        await service.create(command=CreateDepartmentCommand(name="HR"))
    pub.assert_count(0)


# ---------------------------------------------------------------------------
# Get / list
# ---------------------------------------------------------------------------


async def test_get_returns_department(service: DepartmentService) -> None:
    dept = await service.create(command=CreateDepartmentCommand(name="Engineering"))
    got = await service.get(dept_id=dept.id)
    assert got.id == dept.id


async def test_get_nonexistent_raises_404(service: DepartmentService) -> None:
    with pytest.raises(DepartmentNotFoundError):
        await service.get(dept_id=uuid4())


async def test_list_returns_all(service: DepartmentService) -> None:
    await service.create(command=CreateDepartmentCommand(name="Alpha"))
    await service.create(command=CreateDepartmentCommand(name="Beta"))
    page = await service.list(query=ListDepartmentsQuery())
    assert page.total == 2
    assert len(page.items) == 2


async def test_list_pagination(service: DepartmentService) -> None:
    for name in ["A", "B", "C"]:
        await service.create(command=CreateDepartmentCommand(name=name))
    page = await service.list(query=ListDepartmentsQuery(limit=2, offset=0))
    assert len(page.items) == 2
    assert page.total == 3


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


async def test_update_rename(service: DepartmentService) -> None:
    dept = await service.create(command=CreateDepartmentCommand(name="OldName"))
    updated = await service.update(
        dept_id=dept.id,
        command=UpdateDepartmentCommand(name="NewName"),
    )
    assert updated.name == "NewName"
    assert updated.updated_at == _FIXED_TS


async def test_update_deactivate(service: DepartmentService) -> None:
    dept = await service.create(command=CreateDepartmentCommand(name="Eng"))
    updated = await service.update(
        dept_id=dept.id,
        command=UpdateDepartmentCommand(is_active=False),
    )
    assert updated.is_active is False


async def test_update_set_description(service: DepartmentService) -> None:
    dept = await service.create(command=CreateDepartmentCommand(name="Eng"))
    updated = await service.update(
        dept_id=dept.id,
        command=UpdateDepartmentCommand(description="Core engineers", description_set=True),
    )
    assert updated.description == "Core engineers"


async def test_update_clear_description(service: DepartmentService) -> None:
    dept = await service.create(
        command=CreateDepartmentCommand(name="Eng", description="will be cleared")
    )
    updated = await service.update(
        dept_id=dept.id,
        command=UpdateDepartmentCommand(description=None, description_set=True),
    )
    assert updated.description is None


async def test_update_preserves_description_when_not_set(service: DepartmentService) -> None:
    dept = await service.create(command=CreateDepartmentCommand(name="Eng", description="keep me"))
    updated = await service.update(
        dept_id=dept.id,
        command=UpdateDepartmentCommand(is_active=False),  # description_set=False (default)
    )
    assert updated.description == "keep me"


async def test_update_rename_to_same_name_is_ok(service: DepartmentService) -> None:
    """Renaming a department to its own current name must not raise a conflict."""
    dept = await service.create(command=CreateDepartmentCommand(name="Eng"))
    updated = await service.update(
        dept_id=dept.id,
        command=UpdateDepartmentCommand(name="Eng"),
    )
    assert updated.name == "Eng"


async def test_update_duplicate_name_raises_conflict(service: DepartmentService) -> None:
    await service.create(command=CreateDepartmentCommand(name="Alpha"))
    beta = await service.create(command=CreateDepartmentCommand(name="Beta"))
    with pytest.raises(DepartmentNameConflictError):
        await service.update(
            dept_id=beta.id,
            command=UpdateDepartmentCommand(name="Alpha"),
        )


async def test_update_nonexistent_raises_404(service: DepartmentService) -> None:
    with pytest.raises(DepartmentNotFoundError):
        await service.update(dept_id=uuid4(), command=UpdateDepartmentCommand(name="x"))


async def test_update_emits_updated_event_with_diff(
    service: DepartmentService, pub: FakePublisher
) -> None:
    dept = await service.create(command=CreateDepartmentCommand(name="OldName"))
    pub.events.clear()
    await service.update(dept_id=dept.id, command=UpdateDepartmentCommand(name="NewName"))
    evt = pub.assert_emitted("updated", dept.id)
    assert evt["entity_type"] == "department"
    before = evt["before"]
    after = evt["after"]
    assert isinstance(before, dict) and before["name"] == "OldName"
    assert isinstance(after, dict) and after["name"] == "NewName"


async def test_update_same_name_no_event(service: DepartmentService, pub: FakePublisher) -> None:
    """PATCH with the exact same name must not emit an event (no diff)."""
    dept = await service.create(command=CreateDepartmentCommand(name="Eng"))
    pub.events.clear()
    await service.update(dept_id=dept.id, command=UpdateDepartmentCommand(name="Eng"))
    pub.assert_count(0)


async def test_update_noop_patch_no_event(service: DepartmentService, pub: FakePublisher) -> None:
    """PATCH with no changed fields must not emit an event."""
    dept = await service.create(command=CreateDepartmentCommand(name="Eng", description="desc"))
    pub.events.clear()
    # is_active already True, description_set=False, name unchanged
    await service.update(dept_id=dept.id, command=UpdateDepartmentCommand())
    pub.assert_count(0)


async def test_update_deactivate_emits_event(
    service: DepartmentService, pub: FakePublisher
) -> None:
    dept = await service.create(command=CreateDepartmentCommand(name="Eng"))
    pub.events.clear()
    await service.update(dept_id=dept.id, command=UpdateDepartmentCommand(is_active=False))
    evt = pub.assert_emitted("updated", dept.id)
    before = evt["before"]
    after = evt["after"]
    assert isinstance(before, dict) and before["is_active"] is True
    assert isinstance(after, dict) and after["is_active"] is False


# ---------------------------------------------------------------------------
# Membership — add/remove idempotency
# ---------------------------------------------------------------------------


async def test_add_member_happy(service: DepartmentService, repo: FakeRepo) -> None:
    dept = await service.create(command=CreateDepartmentCommand(name="Eng"))
    sub = "user-sub-001"
    m = await service.add_member(dept_id=dept.id, subject=sub)
    assert m.department_id == dept.id
    assert m.subject == sub
    assert await repo.get_membership(dept.id, sub) is not None


async def test_add_member_idempotent(service: DepartmentService) -> None:
    """Adding the same member twice must return the existing membership without error."""
    dept = await service.create(command=CreateDepartmentCommand(name="Eng"))
    m1 = await service.add_member(dept_id=dept.id, subject="sub-A")
    m2 = await service.add_member(dept_id=dept.id, subject="sub-A")
    assert m1.id == m2.id  # same record returned


async def test_add_member_to_nonexistent_dept_raises_404(service: DepartmentService) -> None:
    with pytest.raises(DepartmentNotFoundError):
        await service.add_member(dept_id=uuid4(), subject="sub-A")


async def test_remove_member_happy(service: DepartmentService, repo: FakeRepo) -> None:
    dept = await service.create(command=CreateDepartmentCommand(name="Eng"))
    await service.add_member(dept_id=dept.id, subject="sub-A")
    await service.remove_member(dept_id=dept.id, subject="sub-A")
    assert await repo.get_membership(dept.id, "sub-A") is None


async def test_remove_member_idempotent(service: DepartmentService) -> None:
    """Removing a subject that is not a member is a no-op (no error)."""
    dept = await service.create(command=CreateDepartmentCommand(name="Eng"))
    # subject was never added — should not raise
    await service.remove_member(dept_id=dept.id, subject="sub-not-a-member")


async def test_remove_member_from_nonexistent_dept_raises_404(
    service: DepartmentService,
) -> None:
    with pytest.raises(DepartmentNotFoundError):
        await service.remove_member(dept_id=uuid4(), subject="sub-A")


async def test_add_member_emits_membership_added(
    service: DepartmentService, pub: FakePublisher
) -> None:
    dept = await service.create(command=CreateDepartmentCommand(name="Eng"))
    pub.events.clear()
    await service.add_member(dept_id=dept.id, subject="sub-Z")
    evt = pub.assert_emitted("membership.added", dept.id)
    assert evt["entity_type"] == "department"
    after = evt["after"]
    assert isinstance(after, dict) and after["subject"] == "sub-Z"
    assert evt["before"] is None


async def test_add_member_duplicate_no_event(
    service: DepartmentService, pub: FakePublisher
) -> None:
    """Idempotent re-add must NOT emit an event."""
    dept = await service.create(command=CreateDepartmentCommand(name="Eng"))
    await service.add_member(dept_id=dept.id, subject="sub-A")
    pub.events.clear()  # reset after first add
    await service.add_member(dept_id=dept.id, subject="sub-A")  # duplicate
    pub.assert_count(0)


async def test_remove_member_emits_membership_removed(
    service: DepartmentService, pub: FakePublisher
) -> None:
    dept = await service.create(command=CreateDepartmentCommand(name="Eng"))
    await service.add_member(dept_id=dept.id, subject="sub-B")
    pub.events.clear()
    await service.remove_member(dept_id=dept.id, subject="sub-B")
    evt = pub.assert_emitted("membership.removed", dept.id)
    assert evt["entity_type"] == "department"
    before = evt["before"]
    assert isinstance(before, dict) and before["subject"] == "sub-B"
    assert evt["after"] is None


async def test_remove_nonmember_no_event(service: DepartmentService, pub: FakePublisher) -> None:
    """Idempotent remove of non-member must NOT emit an event."""
    dept = await service.create(command=CreateDepartmentCommand(name="Eng"))
    pub.events.clear()
    await service.remove_member(dept_id=dept.id, subject="sub-not-a-member")
    pub.assert_count(0)
