"""Port contracts for the intake_templates module.

``TemplateRepository`` is the only persistence port; it is implemented in the
infrastructure layer and injected into the service at composition time.

``SubmissionValidator`` is consumed by the ``tickets`` module (Phase 1).
``TemplateService`` satisfies it structurally.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.modules.intake_templates.domain.entities import FieldError, Template, TemplateVersion


class TemplateRepository(Protocol):
    """Port for template and template-version persistence."""

    # ------------------------------------------------------------------
    # Template operations
    # ------------------------------------------------------------------

    async def add_template(self, template: Template) -> None: ...

    async def get_template_by_id(self, template_id: UUID) -> Template | None: ...

    async def get_template_by_name(self, name: str) -> Template | None: ...

    async def list_templates(
        self,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[Template], int]: ...

    # ------------------------------------------------------------------
    # Version operations
    # ------------------------------------------------------------------

    async def add_version(self, version: TemplateVersion) -> None: ...

    async def get_version_by_id(self, version_id: UUID) -> TemplateVersion | None: ...

    async def get_latest_published_version(self, template_id: UUID) -> TemplateVersion | None: ...

    async def list_versions(self, template_id: UUID) -> list[TemplateVersion]: ...

    async def update_version(self, version: TemplateVersion) -> TemplateVersion: ...


class SubmissionValidator(Protocol):
    """Port consumed by the ``tickets`` module to validate intake submissions.

    ``TemplateService`` satisfies this Protocol structurally — tickets imports
    this contract and injects the service at composition time.
    """

    async def validate_submission(
        self,
        *,
        template_version_id: UUID,
        payload: dict[str, object],
    ) -> list[FieldError]: ...
