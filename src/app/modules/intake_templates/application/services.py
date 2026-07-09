"""Intake-template application service.

Orchestrates domain logic, enforces invariants, and delegates persistence to
the TemplateRepository port. The injected ``clock`` makes time deterministic
in tests without monkeypatching.

Rules enforced here:
- Template names are unique per tenant schema (search_path provides scope).
- System templates (is_system=True) may not have their published versions
  archived and may not be deleted or renamed.
- Every new template is seeded with draft version 1 containing all mandatory-
  core fields (MANDATORY_CORE_FIELDS) so BA can publish without manual setup.
- ``validate_submission`` validates a payload dict against a template version's
  field schema; it is a service-layer capability consumed by the ``tickets``
  module in Phase 2 and is NOT exposed as an HTTP endpoint in Phase 1.
- Every mutating operation emits a domain event via the injected publisher so
  the audit log is written atomically in the same transaction.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from app.core.events import EventPublisher
from app.modules.intake_templates.application.dtos import (
    CreateTemplateCommand,
    FieldError,
    ListTemplatesQuery,
    TemplateCreated,
    TemplatePage,
    TemplateWithVersions,
    UpdateDraftFieldsCommand,
)
from app.modules.intake_templates.domain.entities import (
    MANDATORY_CORE_FIELDS,
    TEMPLATE_SELECTION_RULES,
    Template,
    TemplateSelectionRule,
    TemplateType,
    TemplateVersion,
)
from app.modules.intake_templates.domain.errors import (
    DuplicateTemplateNameError,
    SystemTemplateProtectedError,
    TemplateNotFoundError,
    TemplateVersionNotFoundError,
)
from app.modules.intake_templates.domain.ports import TemplateRepository


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class TemplateService:
    """All authenticated members see every template; only ba / tenant_admin may mutate.

    The router enforces the role gate via ``_MANAGE_ROLES``; this service does
    not re-check roles — it trusts the caller to have done so.

    ``publisher`` is required.  Pass ``NoopPublisher()`` from ``core.events``
    only when you explicitly want to suppress auditing (e.g. seeding scripts).
    """

    repo: TemplateRepository
    publisher: EventPublisher
    clock: Callable[[], datetime] = field(default=_utc_now)

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    async def create_template(
        self, *, command: CreateTemplateCommand, actor_id: UUID
    ) -> TemplateCreated:
        """Create a new template with an initial draft version 1.

        Draft v1 is pre-populated with all mandatory-core fields so the BA can
        add type-specific fields and publish without manual setup.
        """
        if command.template_type == TemplateType.FREE_FORM:
            raise SystemTemplateProtectedError(
                "TemplateType.FREE_FORM is reserved for the built-in system template "
                "and cannot be used when creating new templates"
            )
        name = command.name.strip()
        existing = await self.repo.get_template_by_name(name)
        if existing is not None:
            raise DuplicateTemplateNameError(
                f"A template named '{name}' already exists in this tenant"
            )
        now = self.clock()
        template = Template.create(
            template_type=command.template_type,
            name=name,
            description=command.description,
            now=now,
        )
        await self.repo.add_template(template)

        version = TemplateVersion.create_draft(
            template_id=template.id,
            version_number=1,
            fields=MANDATORY_CORE_FIELDS,
            created_by=actor_id,
            now=now,
        )
        await self.repo.add_version(version)

        await self.publisher(
            "template",
            template.id,
            "created",
            after={
                "name": template.name,
                "type": template.type,
                "description": template.description,
                "draft_version": version.version_number,
                "field_count": len(version.fields),
            },
        )
        return TemplateCreated(template=template, version=version)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get_template(self, *, template_id: UUID) -> TemplateWithVersions:
        """Return the template and all its versions (any authenticated member)."""
        template = await self._load_template_or_404(template_id)
        versions = await self.repo.list_versions(template_id)
        return TemplateWithVersions(template=template, versions=versions)

    async def list_templates(self, *, query: ListTemplatesQuery) -> TemplatePage:
        """Paginated list of templates (any authenticated member)."""
        items, total = await self.repo.list_templates(limit=query.limit, offset=query.offset)
        return TemplatePage(items=items, total=total, limit=query.limit, offset=query.offset)

    # ------------------------------------------------------------------
    # Draft editing
    # ------------------------------------------------------------------

    async def update_draft_fields(
        self,
        *,
        template_id: UUID,
        version_id: UUID,
        command: UpdateDraftFieldsCommand,
    ) -> TemplateVersion:
        """Replace all fields of a draft version (full replacement).

        Raises SystemTemplateProtectedError if the template is a system template.
        Raises PublishedVersionImmutableError if the version is not draft.
        """
        template = await self._load_template_or_404(template_id)
        if template.is_system:
            raise SystemTemplateProtectedError("Cannot modify fields of a system template")
        version = await self._load_version_or_404(version_id, template_id)
        before_count = len(version.fields)
        before_keys = sorted(f.key for f in version.fields)
        version.update_fields(command.fields, now=self.clock())
        await self.repo.update_version(version)

        after_count = len(version.fields)
        after_keys = sorted(f.key for f in version.fields)
        await self.publisher(
            "template",
            template_id,
            "draft_fields_updated",
            before={
                "version_id": str(version_id),
                "field_count": before_count,
                "keys": before_keys,
            },
            after={"version_id": str(version_id), "field_count": after_count, "keys": after_keys},
        )
        return version

    # ------------------------------------------------------------------
    # Publish
    # ------------------------------------------------------------------

    async def publish_version(self, *, template_id: UUID, version_id: UUID) -> TemplateVersion:
        """Transition a draft version to published.

        Raises SystemTemplateProtectedError if the template is a system template.
        The domain's ``publish()`` enforces the mandatory-core invariant before
        the status change — raises ``MandatoryCoreMissingError`` if any key is
        absent or not required.
        """
        template = await self._load_template_or_404(template_id)
        if template.is_system:
            raise SystemTemplateProtectedError("Cannot publish a version of a system template")
        version = await self._load_version_or_404(version_id, template_id)
        version.publish(now=self.clock())
        await self.repo.update_version(version)

        await self.publisher(
            "template",
            template_id,
            "version_published",
            after={"version_id": str(version_id), "version_number": version.version_number},
        )
        return version

    # ------------------------------------------------------------------
    # Archive
    # ------------------------------------------------------------------

    async def archive_version(self, *, template_id: UUID, version_id: UUID) -> TemplateVersion:
        """Transition a published version to archived.

        System templates (is_system=True) may not have their published versions
        archived — ``SystemTemplateProtectedError`` is raised instead.
        """
        template = await self._load_template_or_404(template_id)
        if template.is_system:
            raise SystemTemplateProtectedError(
                "Cannot archive a published version of a system template"
            )
        version = await self._load_version_or_404(version_id, template_id)
        version.archive(now=self.clock())
        await self.repo.update_version(version)

        await self.publisher(
            "template",
            template_id,
            "version_archived",
            before={"version_id": str(version_id), "version_number": version.version_number},
        )
        return version

    # ------------------------------------------------------------------
    # New draft from published
    # ------------------------------------------------------------------

    async def new_draft_from_published(
        self, *, template_id: UUID, actor_id: UUID
    ) -> TemplateVersion:
        """Create a new draft version whose fields are copied from the latest
        published version.

        Raises SystemTemplateProtectedError if the template is a system template.
        The new draft's ``version_number`` is one more than the current highest
        version number across all versions of the template.
        """
        template = await self._load_template_or_404(template_id)
        if template.is_system:
            raise SystemTemplateProtectedError("Cannot create a new draft from a system template")
        published = await self.repo.get_latest_published_version(template_id)
        if published is None:
            raise TemplateVersionNotFoundError(
                f"No published version found for template {template_id}"
            )
        all_versions = await self.repo.list_versions(template_id)
        next_number = max(v.version_number for v in all_versions) + 1

        draft = TemplateVersion.create_draft(
            template_id=template_id,
            version_number=next_number,
            fields=published.fields,
            created_by=actor_id,
            now=self.clock(),
        )
        await self.repo.add_version(draft)

        await self.publisher(
            "template",
            template_id,
            "draft_created",
            after={
                "version_id": str(draft.id),
                "version_number": draft.version_number,
                "copied_from_version": published.version_number,
                "field_count": len(draft.fields),
            },
        )
        return draft

    # ------------------------------------------------------------------
    # Selection rules
    # ------------------------------------------------------------------

    def get_selection_rules(self) -> tuple[TemplateSelectionRule, ...]:
        """Return the static §6.4 template selection rules.

        These are code-level constants — no DB lookup needed.
        """
        return TEMPLATE_SELECTION_RULES

    # ------------------------------------------------------------------
    # Submission validation (Phase-1 service capability, no HTTP endpoint yet)
    # ------------------------------------------------------------------

    async def validate_submission(
        self, *, template_version_id: UUID, payload: dict[str, object]
    ) -> list[FieldError]:
        """Validate a raw payload dict against a template version's field schema.

        Returns a (possibly empty) list of ``FieldError`` objects.  An empty
        list means the payload is valid.

        Checks performed:
        - Required fields must be present in the payload and non-empty.
        - All payload keys must correspond to a declared field (no unknowns).

        This method is intentionally NOT exposed as an HTTP endpoint in Phase 1.
        It will be consumed by the ``tickets`` module (SubmissionValidator port)
        in Phase 2.
        """
        version = await self.repo.get_version_by_id(template_version_id)
        if version is None:
            raise TemplateVersionNotFoundError(f"Template version {template_version_id} not found")

        errors: list[FieldError] = []
        defined_keys: set[str] = {f.key for f in version.fields}

        for field_def in version.fields:
            if field_def.required:
                value = payload.get(field_def.key)
                if value is None:
                    errors.append(
                        FieldError(
                            key=field_def.key,
                            message="Required field is missing or null",
                        )
                    )
                elif isinstance(value, str) and not value.strip():
                    errors.append(
                        FieldError(
                            key=field_def.key,
                            message="Required field cannot be empty",
                        )
                    )

        # Unknown keys
        for key in payload:
            if key not in defined_keys:
                errors.append(
                    FieldError(
                        key=key,
                        message="Unknown field not declared in the template version",
                    )
                )

        return errors

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _load_template_or_404(self, template_id: UUID) -> Template:
        template = await self.repo.get_template_by_id(template_id)
        if template is None:
            raise TemplateNotFoundError(f"Template {template_id} not found")
        return template

    async def _load_version_or_404(self, version_id: UUID, template_id: UUID) -> TemplateVersion:
        version = await self.repo.get_version_by_id(version_id)
        if version is None or version.template_id != template_id:
            raise TemplateVersionNotFoundError(
                f"Version {version_id} not found for template {template_id}"
            )
        return version
