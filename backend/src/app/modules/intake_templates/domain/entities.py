"""Intake-template domain entities.

No SQLAlchemy / Pydantic / FastAPI imports allowed in this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import NamedTuple
from uuid import UUID, uuid4

from app.modules.intake_templates.domain.errors import (
    MandatoryCoreMissingError,
    PublishedVersionImmutableError,
    VersionNotDraftError,
    VersionNotPublishedError,
)

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TemplateType(StrEnum):
    FEATURE_REQUEST = "feature_request"
    CHANGE = "change"
    DEFECT = "defect"
    INTEGRATION_DATA = "integration_data"
    ANALYTICS_REQUEST = "analytics_request"
    FREE_FORM = "free_form"


class TemplateVersionStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class FieldKind(StrEnum):
    TEXT = "text"
    TEXTAREA = "textarea"
    NUMBER = "number"
    DATE = "date"
    SELECT = "select"
    MULTISELECT = "multiselect"


# ---------------------------------------------------------------------------
# Mandatory-core field keys (§5 + §6.3 of AI_Business_Analyst_System.md).
#
# Every published version MUST contain ALL of these keys with required=True.
# `business_goal` is a plain required text field in Phase 1; a nullable FK to
# bg_item_id on requirements artifacts arrives in Phase 2 (MVP).
# ---------------------------------------------------------------------------

MANDATORY_CORE_KEYS: tuple[str, ...] = (
    "problem",
    "expected_result",
    "success_metric",
    "as_is",
    "to_be",
    "affected_systems",
    "urgency_deadline",
    "acceptance_criteria",
    "business_goal",
)


# ---------------------------------------------------------------------------
# FieldDefinition — frozen (immutable) value object
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldDefinition:
    """A single field specification within a template version.

    `config` holds kind-specific options (e.g. ``{"options": ["A", "B"]}`` for
    select/multiselect). Other kinds typically leave it empty.

    The `config` field is excluded from hashing but included in equality checks.
    Two definitions with the same key/label/kind/required but different configs
    are NOT equal, but they hash to the same bucket — allowing FieldDefinition
    instances to be placed in sets when needed despite `config` being a dict
    (unhashable by default).
    """

    key: str
    label: str
    kind: FieldKind
    required: bool
    config: dict[str, object] = field(default_factory=dict, hash=False)


# ---------------------------------------------------------------------------
# Canonical mandatory-core FieldDefinition instances.
# Used to seed every new template's initial draft version and the built-in
# free_form system template.
# ---------------------------------------------------------------------------

MANDATORY_CORE_FIELDS: tuple[FieldDefinition, ...] = (
    FieldDefinition(
        key="problem",
        label="Проблема / потребность",
        kind=FieldKind.TEXTAREA,
        required=True,
    ),
    FieldDefinition(
        key="expected_result",
        label="Ожидаемый результат",
        kind=FieldKind.TEXTAREA,
        required=True,
    ),
    FieldDefinition(
        key="success_metric",
        label="Метрика успеха (числовая)",
        kind=FieldKind.TEXT,
        required=True,
    ),
    FieldDefinition(
        key="as_is",
        label="Текущее состояние (AS-IS)",
        kind=FieldKind.TEXTAREA,
        required=True,
    ),
    FieldDefinition(
        key="to_be",
        label="Целевое состояние (TO-BE)",
        kind=FieldKind.TEXTAREA,
        required=True,
    ),
    FieldDefinition(
        key="affected_systems",
        label="Затронутые системы и стейкхолдеры",
        kind=FieldKind.TEXTAREA,
        required=True,
    ),
    FieldDefinition(
        key="urgency_deadline",
        label="Срочность и дедлайн",
        kind=FieldKind.TEXT,
        required=True,
    ),
    FieldDefinition(
        key="acceptance_criteria",
        label="Критерии приёмки (AC)",
        kind=FieldKind.TEXTAREA,
        required=True,
    ),
    FieldDefinition(
        key="business_goal",
        label="Цель бизнеса (BG)",
        # Phase 1: plain required text field.
        # Phase 2 (MVP): nullable bg_item_id FK on requirements supersedes this.
        kind=FieldKind.TEXT,
        required=True,
    ),
)


# ---------------------------------------------------------------------------
# FieldError — value object returned by validate_submission.
# Lives in domain so the SubmissionValidator port can reference it without
# importing application-layer DTOs.
# ---------------------------------------------------------------------------


@dataclass(slots=True, kw_only=True)
class FieldError:
    """A single field-level validation error returned by submission validation."""

    key: str
    message: str


# ---------------------------------------------------------------------------
# Fixed UUIDs for the built-in free_form system template.
# Deterministic so every tenant schema gets the same seed after migration.
# ---------------------------------------------------------------------------

FREE_FORM_TEMPLATE_ID: UUID = UUID("10000000-0000-0000-0000-000000000001")
FREE_FORM_VERSION_ID: UUID = UUID("20000000-0000-0000-0000-000000000001")


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# TemplateVersion — version entity with state-transition methods
# ---------------------------------------------------------------------------


@dataclass(slots=True, kw_only=True)
class TemplateVersion:
    """A single versioned snapshot of a template's field schema.

    State transitions (enforced by invariants):
      draft → published  via ``publish()``   — validates mandatory-core keys
      published → archived  via ``archive()``
      draft only  via ``update_fields()``    — published versions are immutable

    Once a version is published, its ``fields`` tuple is immutable; all edits
    must go through a new draft created from the published baseline.
    """

    id: UUID
    template_id: UUID
    version_number: int
    status: TemplateVersionStatus
    fields: tuple[FieldDefinition, ...]
    created_by: UUID
    created_at: datetime
    updated_at: datetime

    def publish(self, *, now: datetime | None = None) -> None:
        """Transition draft → published.

        Validates that all ``MANDATORY_CORE_KEYS`` are present with
        ``required=True`` before the transition.
        """
        if self.status != TemplateVersionStatus.DRAFT:
            raise VersionNotDraftError(
                f"Only draft versions can be published "
                f"(version {self.version_number} is {self.status!s})"
            )
        self._assert_mandatory_core()
        self.status = TemplateVersionStatus.PUBLISHED
        self.updated_at = now or _now()

    def archive(self, *, now: datetime | None = None) -> None:
        """Transition published → archived."""
        if self.status != TemplateVersionStatus.PUBLISHED:
            raise VersionNotPublishedError(
                f"Only published versions can be archived "
                f"(version {self.version_number} is {self.status!s})"
            )
        self.status = TemplateVersionStatus.ARCHIVED
        self.updated_at = now or _now()

    def update_fields(
        self,
        new_fields: tuple[FieldDefinition, ...],
        *,
        now: datetime | None = None,
    ) -> None:
        """Replace the field list. Only allowed while in draft status.

        Published versions are immutable — call ``new_draft_from_published``
        on the service to create a new editable copy.
        """
        if self.status != TemplateVersionStatus.DRAFT:
            raise PublishedVersionImmutableError(
                f"Cannot edit version {self.version_number}: "
                "published versions are immutable — create a new draft instead."
            )
        self.fields = new_fields
        self.updated_at = now or _now()

    def _assert_mandatory_core(self) -> None:
        """Raise MandatoryCoreMissingError if any mandatory-core key is absent or not required."""
        field_map: dict[str, FieldDefinition] = {f.key: f for f in self.fields}
        missing = [
            k for k in MANDATORY_CORE_KEYS if k not in field_map or not field_map[k].required
        ]
        if missing:
            raise MandatoryCoreMissingError(
                f"Published version must contain all mandatory-core keys with "
                f"required=True; missing or not required: {missing}",
                missing_keys=missing,
            )

    @classmethod
    def create_draft(
        cls,
        *,
        template_id: UUID,
        version_number: int,
        fields: tuple[FieldDefinition, ...],
        created_by: UUID,
        now: datetime | None = None,
    ) -> TemplateVersion:
        """Factory: create a new draft version."""
        ts = now or _now()
        return cls(
            id=uuid4(),
            template_id=template_id,
            version_number=version_number,
            status=TemplateVersionStatus.DRAFT,
            fields=fields,
            created_by=created_by,
            created_at=ts,
            updated_at=ts,
        )


# ---------------------------------------------------------------------------
# Template — aggregate root
# ---------------------------------------------------------------------------


@dataclass(slots=True, kw_only=True)
class Template:
    """Aggregate root representing one catalog entry in the template library."""

    id: UUID
    type: TemplateType
    name: str
    description: str | None
    is_system: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def create(
        cls,
        *,
        template_type: TemplateType,
        name: str,
        description: str | None = None,
        is_system: bool = False,
        now: datetime | None = None,
    ) -> Template:
        ts = now or _now()
        return cls(
            id=uuid4(),
            type=template_type,
            name=name.strip(),
            description=description,
            is_system=is_system,
            created_at=ts,
            updated_at=ts,
        )


# ---------------------------------------------------------------------------
# Template selection rules (§6.4 of AI_Business_Analyst_System.md).
# Code-level constant — no DB table needed in Phase 1.
# ---------------------------------------------------------------------------


class TemplateSelectionRule(NamedTuple):
    """One row of the §6.4 template-selection table.

    Mirrors AI_Business_Analyst_System.md §6.4 row-for-row: a business
    situation maps to a recommended documentation template and a
    requirements level. ``intake_template_type`` is not part of the §6.4
    table — it is the intake form type (§5 catalog) the BA triage feature
    should suggest for the situation.
    """

    situation: str
    doc_template: str
    level: str
    intake_template_type: TemplateType


TEMPLATE_SELECTION_RULES: tuple[TemplateSelectionRule, ...] = (
    TemplateSelectionRule(
        situation="Новая идея или боль — нужен бизнес-кейс",
        doc_template="BRD",
        level="business",
        intake_template_type=TemplateType.FEATURE_REQUEST,
    ),
    TemplateSelectionRule(
        situation="Новое приложение или крупная система",
        doc_template="SRS (+ BRD)",
        level="system",
        intake_template_type=TemplateType.FEATURE_REQUEST,
    ),
    TemplateSelectionRule(
        situation="Описать поведение и сценарии",
        doc_template="Use Case",
        level="scenario",
        intake_template_type=TemplateType.FEATURE_REQUEST,
    ),
    TemplateSelectionRule(
        situation="Мелкая доработка (одно-два изменения)",
        doc_template="Change Specification",
        level="task",
        intake_template_type=TemplateType.CHANGE,
    ),
    TemplateSelectionRule(
        situation="Более двух изменений или новый модуль",
        doc_template="SRS",
        level="system",
        intake_template_type=TemplateType.CHANGE,
    ),
    TemplateSelectionRule(
        situation="Нетиповой запрос",
        doc_template="Свободная форма + ядро",
        level="any",
        intake_template_type=TemplateType.FREE_FORM,
    ),
)
