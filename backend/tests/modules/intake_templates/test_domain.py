"""Domain-layer tests for intake_templates.

Covers the mandatory-core invariant, state-transition guards on TemplateVersion,
system-template protection semantics (tested via error types), and FieldDefinition
immutability.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.modules.intake_templates.domain.entities import (
    FREE_FORM_TEMPLATE_ID,
    MANDATORY_CORE_FIELDS,
    MANDATORY_CORE_KEYS,
    TEMPLATE_SELECTION_RULES,
    FieldDefinition,
    FieldKind,
    Template,
    TemplateSelectionRule,
    TemplateType,
    TemplateVersion,
    TemplateVersionStatus,
)
from app.modules.intake_templates.domain.errors import (
    MandatoryCoreMissingError,
    PublishedVersionImmutableError,
    VersionNotDraftError,
    VersionNotPublishedError,
)

_NOW = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_version(
    *,
    status: TemplateVersionStatus = TemplateVersionStatus.DRAFT,
    fields: tuple[FieldDefinition, ...] = MANDATORY_CORE_FIELDS,
) -> TemplateVersion:
    tid = uuid4()
    return TemplateVersion(
        id=uuid4(),
        template_id=tid,
        version_number=1,
        status=status,
        fields=fields,
        created_by=uuid4(),
        created_at=_NOW,
        updated_at=_NOW,
    )


def _make_field(key: str, required: bool = True) -> FieldDefinition:
    return FieldDefinition(key=key, label=key.capitalize(), kind=FieldKind.TEXT, required=required)


# ---------------------------------------------------------------------------
# FieldDefinition
# ---------------------------------------------------------------------------


class TestFieldDefinition:
    def test_frozen_assignment_raises(self) -> None:
        import dataclasses

        fd = _make_field("x")
        with pytest.raises(dataclasses.FrozenInstanceError):
            fd.key = "y"  # type: ignore[misc]

    def test_default_config_is_empty_dict(self) -> None:
        fd = _make_field("x")
        assert fd.config == {}

    def test_equality_based_on_all_fields(self) -> None:
        a = FieldDefinition(key="x", label="X", kind=FieldKind.TEXT, required=True)
        b = FieldDefinition(key="x", label="X", kind=FieldKind.TEXT, required=True)
        assert a == b

    def test_inequality_on_kind(self) -> None:
        a = FieldDefinition(key="x", label="X", kind=FieldKind.TEXT, required=True)
        b = FieldDefinition(key="x", label="X", kind=FieldKind.TEXTAREA, required=True)
        assert a != b


# ---------------------------------------------------------------------------
# Mandatory-core constant
# ---------------------------------------------------------------------------


class TestMandatoryCoreKeys:
    def test_nine_keys_defined(self) -> None:
        assert len(MANDATORY_CORE_KEYS) == 9

    def test_business_goal_is_present(self) -> None:
        assert "business_goal" in MANDATORY_CORE_KEYS

    def test_mandatory_core_fields_covers_all_keys(self) -> None:
        keys = {f.key for f in MANDATORY_CORE_FIELDS}
        assert keys == set(MANDATORY_CORE_KEYS)

    def test_all_mandatory_fields_are_required(self) -> None:
        for f in MANDATORY_CORE_FIELDS:
            assert f.required, f"mandatory field '{f.key}' must have required=True"


# ---------------------------------------------------------------------------
# Publish transition
# ---------------------------------------------------------------------------


class TestPublish:
    def test_publish_draft_succeeds(self) -> None:
        v = _make_version()
        v.publish(now=_NOW)
        assert v.status is TemplateVersionStatus.PUBLISHED

    def test_publish_updates_updated_at(self) -> None:
        later = datetime(2026, 7, 2, 13, 0, tzinfo=UTC)
        v = _make_version()
        v.publish(now=later)
        assert v.updated_at == later

    def test_publish_already_published_raises(self) -> None:
        v = _make_version(status=TemplateVersionStatus.PUBLISHED)
        with pytest.raises(VersionNotDraftError):
            v.publish()

    def test_publish_archived_raises(self) -> None:
        v = _make_version(status=TemplateVersionStatus.ARCHIVED)
        with pytest.raises(VersionNotDraftError):
            v.publish()

    def test_publish_missing_mandatory_key_raises(self) -> None:
        """Remove one mandatory-core field — publish must be blocked."""
        fields_without_problem = tuple(f for f in MANDATORY_CORE_FIELDS if f.key != "problem")
        v = _make_version(fields=fields_without_problem)
        with pytest.raises(MandatoryCoreMissingError) as exc_info:
            v.publish()
        assert "problem" in exc_info.value.missing_keys

    def test_publish_non_required_mandatory_key_raises(self) -> None:
        """A mandatory key present but with required=False must also block publish."""
        fields = tuple(
            FieldDefinition(
                key=f.key,
                label=f.label,
                kind=f.kind,
                required=False if f.key == "success_metric" else f.required,
            )
            for f in MANDATORY_CORE_FIELDS
        )
        v = _make_version(fields=fields)
        with pytest.raises(MandatoryCoreMissingError) as exc_info:
            v.publish()
        assert "success_metric" in exc_info.value.missing_keys

    def test_mandatory_core_missing_error_details(self) -> None:
        fields = tuple(f for f in MANDATORY_CORE_FIELDS if f.key not in {"problem", "as_is"})
        v = _make_version(fields=fields)
        with pytest.raises(MandatoryCoreMissingError) as exc_info:
            v.publish()
        err = exc_info.value
        assert "problem" in err.missing_keys
        assert "as_is" in err.missing_keys
        assert len(err.details) == 1
        assert err.details[0]["missing_keys"] == err.missing_keys


# ---------------------------------------------------------------------------
# Archive transition
# ---------------------------------------------------------------------------


class TestArchive:
    def test_archive_published_succeeds(self) -> None:
        v = _make_version(status=TemplateVersionStatus.PUBLISHED)
        v.archive(now=_NOW)
        assert v.status is TemplateVersionStatus.ARCHIVED

    def test_archive_draft_raises(self) -> None:
        v = _make_version(status=TemplateVersionStatus.DRAFT)
        with pytest.raises(VersionNotPublishedError):
            v.archive()

    def test_archive_already_archived_raises(self) -> None:
        v = _make_version(status=TemplateVersionStatus.ARCHIVED)
        with pytest.raises(VersionNotPublishedError):
            v.archive()


# ---------------------------------------------------------------------------
# update_fields (published immutability)
# ---------------------------------------------------------------------------


class TestUpdateFields:
    def test_update_draft_succeeds(self) -> None:
        v = _make_version()
        new_fields = (_make_field("custom_field"), *MANDATORY_CORE_FIELDS)
        v.update_fields(new_fields, now=_NOW)
        assert v.fields == new_fields

    def test_update_published_raises(self) -> None:
        v = _make_version(status=TemplateVersionStatus.PUBLISHED)
        with pytest.raises(PublishedVersionImmutableError):
            v.update_fields(MANDATORY_CORE_FIELDS)

    def test_update_archived_raises(self) -> None:
        v = _make_version(status=TemplateVersionStatus.ARCHIVED)
        with pytest.raises(PublishedVersionImmutableError):
            v.update_fields(MANDATORY_CORE_FIELDS)


# ---------------------------------------------------------------------------
# Template.create factory
# ---------------------------------------------------------------------------


class TestTemplate:
    def test_create_sets_defaults(self) -> None:
        t = Template.create(
            template_type=TemplateType.CHANGE,
            name="Change Request",
            now=_NOW,
        )
        assert t.type is TemplateType.CHANGE
        assert t.is_system is False
        assert t.created_at == _NOW

    def test_create_strips_name(self) -> None:
        t = Template.create(template_type=TemplateType.DEFECT, name="  Bug Fix  ")
        assert t.name == "Bug Fix"

    def test_create_system_flag(self) -> None:
        t = Template.create(
            template_type=TemplateType.FREE_FORM,
            name="Free Form",
            is_system=True,
        )
        assert t.is_system is True


# ---------------------------------------------------------------------------
# TemplateVersion.create_draft factory
# ---------------------------------------------------------------------------


class TestCreateDraft:
    def test_create_draft_is_draft_status(self) -> None:
        tid = uuid4()
        v = TemplateVersion.create_draft(
            template_id=tid,
            version_number=1,
            fields=MANDATORY_CORE_FIELDS,
            created_by=uuid4(),
            now=_NOW,
        )
        assert v.status is TemplateVersionStatus.DRAFT
        assert v.template_id == tid
        assert v.version_number == 1
        assert v.fields == MANDATORY_CORE_FIELDS


# ---------------------------------------------------------------------------
# Selection rules
# ---------------------------------------------------------------------------


class TestSelectionRules:
    def test_mirrors_section_6_4_row_count(self) -> None:
        assert len(TEMPLATE_SELECTION_RULES) == 6  # exactly the §6.4 rows

    def test_all_are_selection_rule_instances(self) -> None:
        for rule in TEMPLATE_SELECTION_RULES:
            assert isinstance(rule, TemplateSelectionRule)

    def test_doc_templates_match_section_6_4(self) -> None:
        docs = [r.doc_template for r in TEMPLATE_SELECTION_RULES]
        assert docs == [
            "BRD",
            "SRS (+ BRD)",
            "Use Case",
            "Change Specification",
            "SRS",
            "Свободная форма + ядро",
        ]

    def test_free_form_rule_present(self) -> None:
        types = {r.intake_template_type for r in TEMPLATE_SELECTION_RULES}
        assert TemplateType.FREE_FORM in types

    def test_feature_request_rule_present(self) -> None:
        types = {r.intake_template_type for r in TEMPLATE_SELECTION_RULES}
        assert TemplateType.FEATURE_REQUEST in types


# ---------------------------------------------------------------------------
# Fixed UUIDs
# ---------------------------------------------------------------------------


class TestFixedUUIDs:
    def test_free_form_template_id_stable(self) -> None:
        from uuid import UUID

        assert UUID("10000000-0000-0000-0000-000000000001") == FREE_FORM_TEMPLATE_ID
