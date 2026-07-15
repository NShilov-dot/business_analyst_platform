"""Service-level tests for TemplateService using an in-memory fake repository.

Covers:
- create_template seeds mandatory-core fields in draft v1; rejects FREE_FORM type
- duplicate name raises DuplicateTemplateNameError
- get_template not found raises TemplateNotFoundError
- update_draft_fields replaces fields; blocks on system templates
- publish_version enforces mandatory-core invariant; blocks on system templates
- archive_version blocks on system templates
- new_draft_from_published copies fields from latest published; blocks on system templates
- validate_submission: missing required fields, empty required fields, unknown keys
- Domain event emission on every mutating path (and not on errors)
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.modules.intake_templates.application.dtos import (
    CreateTemplateCommand,
    ListTemplatesQuery,
    UpdateDraftFieldsCommand,
)
from app.modules.intake_templates.application.services import TemplateService
from app.modules.intake_templates.domain.entities import (
    MANDATORY_CORE_FIELDS,
    MANDATORY_CORE_KEYS,
    FieldDefinition,
    FieldKind,
    Template,
    TemplateType,
    TemplateVersion,
    TemplateVersionStatus,
)
from app.modules.intake_templates.domain.errors import (
    DuplicateTemplateNameError,
    MandatoryCoreMissingError,
    PublishedVersionImmutableError,
    SystemTemplateProtectedError,
    TemplateNotFoundError,
    TemplateVersionNotFoundError,
)

_TS = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)
_ACTOR = uuid4()


# ---------------------------------------------------------------------------
# Fake repository
# ---------------------------------------------------------------------------


class FakeRepo:
    def __init__(self) -> None:
        self._templates: dict[UUID, Template] = {}
        self._versions: dict[UUID, TemplateVersion] = {}

    async def add_template(self, template: Template) -> None:
        for t in self._templates.values():
            if t.name == template.name:
                raise DuplicateTemplateNameError(
                    f"A template named '{template.name}' already exists"
                )
        self._templates[template.id] = template

    async def get_template_by_id(self, template_id: UUID) -> Template | None:
        return self._templates.get(template_id)

    async def get_template_by_name(self, name: str) -> Template | None:
        for t in self._templates.values():
            if t.name == name:
                return t
        return None

    async def list_templates(self, *, limit: int, offset: int) -> tuple[list[Template], int]:
        items = sorted(self._templates.values(), key=lambda t: t.created_at)
        return items[offset : offset + limit], len(items)

    async def add_version(self, version: TemplateVersion) -> None:
        self._versions[version.id] = version

    async def get_version_by_id(self, version_id: UUID) -> TemplateVersion | None:
        return self._versions.get(version_id)

    async def get_latest_published_version(self, template_id: UUID) -> TemplateVersion | None:
        published = [
            v
            for v in self._versions.values()
            if v.template_id == template_id and v.status is TemplateVersionStatus.PUBLISHED
        ]
        if not published:
            return None
        return max(published, key=lambda v: v.version_number)

    async def list_versions(self, template_id: UUID) -> list[TemplateVersion]:
        return sorted(
            [v for v in self._versions.values() if v.template_id == template_id],
            key=lambda v: v.version_number,
        )

    async def update_version(self, version: TemplateVersion) -> TemplateVersion:
        self._versions[version.id] = version
        return version


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
            f"No event with action={action!r}. Got: {[e['action'] for e in self.events]}"
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


@pytest.fixture
def repo() -> FakeRepo:
    return FakeRepo()


@pytest.fixture
def pub() -> FakePublisher:
    return FakePublisher()


@pytest.fixture
def service(repo: FakeRepo, pub: FakePublisher) -> TemplateService:
    return TemplateService(repo=repo, clock=lambda: _TS, publisher=pub)


# ---------------------------------------------------------------------------
# create_template
# ---------------------------------------------------------------------------


async def test_create_template_persists(service: TemplateService, repo: FakeRepo) -> None:
    result = await service.create_template(
        command=CreateTemplateCommand(template_type=TemplateType.CHANGE, name="Change"),
        actor_id=_ACTOR,
    )
    assert await repo.get_template_by_id(result.template.id) is not None
    assert result.template.type is TemplateType.CHANGE


async def test_create_template_seeds_mandatory_core(
    service: TemplateService, repo: FakeRepo
) -> None:
    result = await service.create_template(
        command=CreateTemplateCommand(template_type=TemplateType.FEATURE_REQUEST, name="Feature"),
        actor_id=_ACTOR,
    )
    version = result.version
    assert version.status is TemplateVersionStatus.DRAFT
    assert version.version_number == 1
    seeded_keys = {f.key for f in version.fields}
    for key in MANDATORY_CORE_KEYS:
        assert key in seeded_keys, f"mandatory key '{key}' not seeded in draft v1"


async def test_create_template_draft_v1_actor(service: TemplateService, repo: FakeRepo) -> None:
    result = await service.create_template(
        command=CreateTemplateCommand(template_type=TemplateType.DEFECT, name="Defect"),
        actor_id=_ACTOR,
    )
    assert result.version.created_by == _ACTOR


async def test_create_template_duplicate_name_raises(
    service: TemplateService,
) -> None:
    await service.create_template(
        command=CreateTemplateCommand(template_type=TemplateType.CHANGE, name="Same"),
        actor_id=_ACTOR,
    )
    with pytest.raises(DuplicateTemplateNameError):
        await service.create_template(
            command=CreateTemplateCommand(template_type=TemplateType.CHANGE, name="Same"),
            actor_id=_ACTOR,
        )


async def test_create_template_strips_whitespace(service: TemplateService) -> None:
    result = await service.create_template(
        command=CreateTemplateCommand(template_type=TemplateType.CHANGE, name="  Spaces  "),
        actor_id=_ACTOR,
    )
    assert result.template.name == "Spaces"


async def test_create_template_emits_created_event(
    service: TemplateService, pub: FakePublisher
) -> None:
    result = await service.create_template(
        command=CreateTemplateCommand(template_type=TemplateType.CHANGE, name="T"),
        actor_id=_ACTOR,
    )
    evt = pub.assert_emitted("created", result.template.id)
    assert evt["entity_type"] == "template"
    assert evt["before"] is None
    after = evt["after"]
    assert isinstance(after, dict)
    assert after["name"] == "T"
    assert after["field_count"] == len(MANDATORY_CORE_FIELDS)


async def test_create_template_duplicate_no_event(
    service: TemplateService, pub: FakePublisher
) -> None:
    await service.create_template(
        command=CreateTemplateCommand(template_type=TemplateType.CHANGE, name="Same"),
        actor_id=_ACTOR,
    )
    pub.events.clear()
    with pytest.raises(DuplicateTemplateNameError):
        await service.create_template(
            command=CreateTemplateCommand(template_type=TemplateType.CHANGE, name="Same"),
            actor_id=_ACTOR,
        )
    pub.assert_count(0)


# ---------------------------------------------------------------------------
# get_template / list_templates
# ---------------------------------------------------------------------------


async def test_get_template_not_found_raises(service: TemplateService) -> None:
    with pytest.raises(TemplateNotFoundError):
        await service.get_template(template_id=uuid4())


async def test_get_template_returns_template_and_versions(
    service: TemplateService, repo: FakeRepo
) -> None:
    result = await service.create_template(
        command=CreateTemplateCommand(template_type=TemplateType.CHANGE, name="T1"),
        actor_id=_ACTOR,
    )
    detail = await service.get_template(template_id=result.template.id)
    assert detail.template.id == result.template.id
    assert len(detail.versions) == 1


async def test_list_templates_pagination(service: TemplateService) -> None:
    for name in ["A", "B", "C"]:
        await service.create_template(
            command=CreateTemplateCommand(template_type=TemplateType.CHANGE, name=name),
            actor_id=_ACTOR,
        )
    page = await service.list_templates(query=ListTemplatesQuery(limit=2, offset=0))
    assert page.total == 3
    assert len(page.items) == 2


# ---------------------------------------------------------------------------
# update_draft_fields
# ---------------------------------------------------------------------------


async def test_update_draft_fields_replaces_fields(
    service: TemplateService, repo: FakeRepo
) -> None:
    result = await service.create_template(
        command=CreateTemplateCommand(template_type=TemplateType.CHANGE, name="T"),
        actor_id=_ACTOR,
    )
    extra_field = FieldDefinition(key="extra", label="Extra", kind=FieldKind.TEXT, required=False)
    new_fields = (*MANDATORY_CORE_FIELDS, extra_field)
    version = await service.update_draft_fields(
        template_id=result.template.id,
        version_id=result.version.id,
        command=UpdateDraftFieldsCommand(fields=new_fields),
    )
    keys = {f.key for f in version.fields}
    assert "extra" in keys


async def test_update_draft_fields_on_published_raises(
    service: TemplateService,
) -> None:
    result = await service.create_template(
        command=CreateTemplateCommand(template_type=TemplateType.CHANGE, name="T"),
        actor_id=_ACTOR,
    )
    await service.publish_version(template_id=result.template.id, version_id=result.version.id)
    with pytest.raises(PublishedVersionImmutableError):
        await service.update_draft_fields(
            template_id=result.template.id,
            version_id=result.version.id,
            command=UpdateDraftFieldsCommand(fields=MANDATORY_CORE_FIELDS),
        )


async def test_update_draft_fields_emits_event(
    service: TemplateService, pub: FakePublisher
) -> None:
    result = await service.create_template(
        command=CreateTemplateCommand(template_type=TemplateType.CHANGE, name="T"),
        actor_id=_ACTOR,
    )
    pub.events.clear()
    extra_field = FieldDefinition(key="extra", label="Extra", kind=FieldKind.TEXT, required=False)
    new_fields = (*MANDATORY_CORE_FIELDS, extra_field)
    await service.update_draft_fields(
        template_id=result.template.id,
        version_id=result.version.id,
        command=UpdateDraftFieldsCommand(fields=new_fields),
    )
    evt = pub.assert_emitted("draft_fields_updated", result.template.id)
    assert evt["entity_type"] == "template"
    before = evt["before"]
    after = evt["after"]
    assert isinstance(before, dict) and before["field_count"] == len(MANDATORY_CORE_FIELDS)
    assert isinstance(after, dict) and after["field_count"] == len(new_fields)
    assert isinstance(after["keys"], list) and "extra" in after["keys"]  # type: ignore[operator]


# ---------------------------------------------------------------------------
# publish_version
# ---------------------------------------------------------------------------


async def test_publish_version_transitions_to_published(
    service: TemplateService,
) -> None:
    result = await service.create_template(
        command=CreateTemplateCommand(template_type=TemplateType.FEATURE_REQUEST, name="T"),
        actor_id=_ACTOR,
    )
    version = await service.publish_version(
        template_id=result.template.id, version_id=result.version.id
    )
    assert version.status is TemplateVersionStatus.PUBLISHED


async def test_publish_version_missing_core_raises(service: TemplateService) -> None:
    result = await service.create_template(
        command=CreateTemplateCommand(template_type=TemplateType.CHANGE, name="T"),
        actor_id=_ACTOR,
    )
    # Strip mandatory core field
    stripped = tuple(f for f in MANDATORY_CORE_FIELDS if f.key != "problem")
    await service.update_draft_fields(
        template_id=result.template.id,
        version_id=result.version.id,
        command=UpdateDraftFieldsCommand(fields=stripped),
    )
    with pytest.raises(MandatoryCoreMissingError):
        await service.publish_version(template_id=result.template.id, version_id=result.version.id)


async def test_publish_version_emits_event(service: TemplateService, pub: FakePublisher) -> None:
    result = await service.create_template(
        command=CreateTemplateCommand(template_type=TemplateType.CHANGE, name="T"),
        actor_id=_ACTOR,
    )
    pub.events.clear()
    await service.publish_version(template_id=result.template.id, version_id=result.version.id)
    evt = pub.assert_emitted("version_published", result.template.id)
    assert evt["entity_type"] == "template"
    after = evt["after"]
    assert isinstance(after, dict) and after["version_number"] == 1
    assert evt["before"] is None


# ---------------------------------------------------------------------------
# archive_version
# ---------------------------------------------------------------------------


async def test_archive_version_transitions_to_archived(
    service: TemplateService,
) -> None:
    result = await service.create_template(
        command=CreateTemplateCommand(template_type=TemplateType.CHANGE, name="T"),
        actor_id=_ACTOR,
    )
    await service.publish_version(template_id=result.template.id, version_id=result.version.id)
    version = await service.archive_version(
        template_id=result.template.id, version_id=result.version.id
    )
    assert version.status is TemplateVersionStatus.ARCHIVED


async def test_archive_system_template_raises(service: TemplateService, repo: FakeRepo) -> None:
    """System templates (is_system=True) cannot have their published version archived."""
    ts = _TS
    sys_template = Template(
        id=uuid4(),
        type=TemplateType.FREE_FORM,
        name="System Free Form",
        description=None,
        is_system=True,
        created_at=ts,
        updated_at=ts,
    )
    await repo.add_template(sys_template)
    sys_version = TemplateVersion.create_draft(
        template_id=sys_template.id,
        version_number=1,
        fields=MANDATORY_CORE_FIELDS,
        created_by=_ACTOR,
        now=ts,
    )
    sys_version.publish(now=ts)
    await repo.add_version(sys_version)

    with pytest.raises(SystemTemplateProtectedError):
        await service.archive_version(template_id=sys_template.id, version_id=sys_version.id)


async def test_archive_version_emits_event(service: TemplateService, pub: FakePublisher) -> None:
    result = await service.create_template(
        command=CreateTemplateCommand(template_type=TemplateType.CHANGE, name="T"),
        actor_id=_ACTOR,
    )
    await service.publish_version(template_id=result.template.id, version_id=result.version.id)
    pub.events.clear()
    await service.archive_version(template_id=result.template.id, version_id=result.version.id)
    evt = pub.assert_emitted("version_archived", result.template.id)
    assert evt["entity_type"] == "template"
    before = evt["before"]
    assert isinstance(before, dict) and before["version_number"] == 1
    assert evt["after"] is None


# ---------------------------------------------------------------------------
# new_draft_from_published
# ---------------------------------------------------------------------------


async def test_new_draft_copies_fields_from_published(
    service: TemplateService,
) -> None:
    result = await service.create_template(
        command=CreateTemplateCommand(template_type=TemplateType.CHANGE, name="T"),
        actor_id=_ACTOR,
    )
    await service.publish_version(template_id=result.template.id, version_id=result.version.id)
    draft = await service.new_draft_from_published(template_id=result.template.id, actor_id=_ACTOR)
    assert draft.status is TemplateVersionStatus.DRAFT
    assert draft.version_number == 2
    assert draft.fields == result.version.fields


async def test_new_draft_no_published_raises(service: TemplateService) -> None:
    result = await service.create_template(
        command=CreateTemplateCommand(template_type=TemplateType.CHANGE, name="T"),
        actor_id=_ACTOR,
    )
    with pytest.raises(TemplateVersionNotFoundError):
        await service.new_draft_from_published(template_id=result.template.id, actor_id=_ACTOR)


async def test_new_draft_emits_draft_created_event(
    service: TemplateService, pub: FakePublisher
) -> None:
    result = await service.create_template(
        command=CreateTemplateCommand(template_type=TemplateType.CHANGE, name="T"),
        actor_id=_ACTOR,
    )
    await service.publish_version(template_id=result.template.id, version_id=result.version.id)
    pub.events.clear()
    draft = await service.new_draft_from_published(template_id=result.template.id, actor_id=_ACTOR)
    evt = pub.assert_emitted("draft_created", result.template.id)
    assert evt["entity_type"] == "template"
    after = evt["after"]
    assert isinstance(after, dict)
    assert after["version_number"] == draft.version_number
    assert after["copied_from_version"] == 1
    assert evt["before"] is None


# ---------------------------------------------------------------------------
# get_selection_rules
# ---------------------------------------------------------------------------


def test_get_selection_rules_returns_all(service: TemplateService) -> None:
    rules = service.get_selection_rules()
    assert len(rules) == 6
    types = {r.intake_template_type for r in rules}
    assert TemplateType.FREE_FORM in types


# ---------------------------------------------------------------------------
# validate_submission
# ---------------------------------------------------------------------------


async def _create_published(service: TemplateService, name: str) -> TemplateVersion:
    result = await service.create_template(
        command=CreateTemplateCommand(template_type=TemplateType.CHANGE, name=name),
        actor_id=_ACTOR,
    )
    return await service.publish_version(
        template_id=result.template.id, version_id=result.version.id
    )


async def test_validate_submission_valid(service: TemplateService) -> None:
    version = await _create_published(service, "V1")
    payload: dict[str, object] = {k: "value" for k in MANDATORY_CORE_KEYS}
    errors = await service.validate_submission(template_version_id=version.id, payload=payload)
    assert errors == []


async def test_validate_submission_missing_required(service: TemplateService) -> None:
    version = await _create_published(service, "V2")
    payload: dict[str, object] = {k: "value" for k in MANDATORY_CORE_KEYS if k != "problem"}
    errors = await service.validate_submission(template_version_id=version.id, payload=payload)
    error_keys = [e.key for e in errors]
    assert "problem" in error_keys


async def test_validate_submission_empty_required_field(
    service: TemplateService,
) -> None:
    version = await _create_published(service, "V3")
    payload: dict[str, object] = {k: "value" for k in MANDATORY_CORE_KEYS}
    payload["expected_result"] = "   "  # whitespace-only
    errors = await service.validate_submission(template_version_id=version.id, payload=payload)
    error_keys = [e.key for e in errors]
    assert "expected_result" in error_keys


async def test_validate_submission_unknown_key(service: TemplateService) -> None:
    version = await _create_published(service, "V4")
    payload: dict[str, object] = {k: "value" for k in MANDATORY_CORE_KEYS}
    payload["unknown_field"] = "something"
    errors = await service.validate_submission(template_version_id=version.id, payload=payload)
    error_keys = [e.key for e in errors]
    assert "unknown_field" in error_keys


async def test_validate_submission_version_not_found_raises(
    service: TemplateService,
) -> None:
    with pytest.raises(TemplateVersionNotFoundError):
        await service.validate_submission(template_version_id=uuid4(), payload={})


async def test_validate_submission_null_required_field(
    service: TemplateService,
) -> None:
    version = await _create_published(service, "V5")
    payload: dict[str, object] = {k: "value" for k in MANDATORY_CORE_KEYS}
    payload["as_is"] = None  # type: ignore[assignment]
    errors = await service.validate_submission(template_version_id=version.id, payload=payload)
    error_keys = [e.key for e in errors]
    assert "as_is" in error_keys


# ---------------------------------------------------------------------------
# System-template protection: free_form type + mutation guards
# ---------------------------------------------------------------------------


async def _make_system_template(repo: FakeRepo) -> tuple[Template, TemplateVersion]:
    """Seed a published system template in the fake repo."""
    sys_template = Template(
        id=uuid4(),
        type=TemplateType.FREE_FORM,
        name="System Free Form",
        description=None,
        is_system=True,
        created_at=_TS,
        updated_at=_TS,
    )
    await repo.add_template(sys_template)
    sys_version = TemplateVersion.create_draft(
        template_id=sys_template.id,
        version_number=1,
        fields=MANDATORY_CORE_FIELDS,
        created_by=_ACTOR,
        now=_TS,
    )
    sys_version.publish(now=_TS)
    await repo.add_version(sys_version)
    return sys_template, sys_version


async def test_create_template_free_form_type_raises(service: TemplateService) -> None:
    """TemplateType.FREE_FORM is reserved — create must reject it."""
    with pytest.raises(SystemTemplateProtectedError):
        await service.create_template(
            command=CreateTemplateCommand(
                template_type=TemplateType.FREE_FORM, name="Custom Free Form"
            ),
            actor_id=_ACTOR,
        )


async def test_update_draft_fields_system_template_raises(
    service: TemplateService, repo: FakeRepo
) -> None:
    """update_draft_fields must raise on a system template."""
    sys_template, sys_version = await _make_system_template(repo)
    with pytest.raises(SystemTemplateProtectedError):
        await service.update_draft_fields(
            template_id=sys_template.id,
            version_id=sys_version.id,
            command=UpdateDraftFieldsCommand(fields=MANDATORY_CORE_FIELDS),
        )


async def test_publish_version_system_template_raises(
    service: TemplateService, repo: FakeRepo
) -> None:
    """publish_version must raise on a system template."""
    sys_template, sys_version = await _make_system_template(repo)
    with pytest.raises(SystemTemplateProtectedError):
        await service.publish_version(template_id=sys_template.id, version_id=sys_version.id)


async def test_new_draft_from_published_system_template_raises(
    service: TemplateService, repo: FakeRepo
) -> None:
    """new_draft_from_published must raise on a system template."""
    sys_template, _ = await _make_system_template(repo)
    with pytest.raises(SystemTemplateProtectedError):
        await service.new_draft_from_published(template_id=sys_template.id, actor_id=_ACTOR)
