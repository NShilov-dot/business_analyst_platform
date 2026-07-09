"""DTOs, commands, queries, and page types for the intake_templates module.

No SQLAlchemy / Pydantic / FastAPI imports allowed here. Plain Python only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple

from app.modules.intake_templates.domain.entities import (
    FieldDefinition,
    FieldError,
    Template,
    TemplateType,
    TemplateVersion,
)

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@dataclass(slots=True, kw_only=True)
class CreateTemplateCommand:
    """Create a new template; the service auto-creates draft version 1."""

    template_type: TemplateType
    name: str
    description: str | None = None


@dataclass(slots=True, kw_only=True)
class UpdateDraftFieldsCommand:
    """Replace all fields of a draft version (full replacement, not a patch)."""

    fields: tuple[FieldDefinition, ...]


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


@dataclass(slots=True, kw_only=True)
class ListTemplatesQuery:
    limit: int = 20
    offset: int = 0


# ---------------------------------------------------------------------------
# Page / composite result types
# ---------------------------------------------------------------------------


class TemplatePage(NamedTuple):
    items: list[Template]
    total: int
    limit: int
    offset: int


class TemplateWithVersions(NamedTuple):
    """The result of get_template: aggregate root + all its versions."""

    template: Template
    versions: list[TemplateVersion]


class TemplateCreated(NamedTuple):
    """Service return value for create_template: the new template + its draft v1."""

    template: Template
    version: TemplateVersion


# ---------------------------------------------------------------------------
# Validation result — re-exported from domain for callers that import from dtos.
# ---------------------------------------------------------------------------

__all__ = [
    "CreateTemplateCommand",
    "FieldError",
    "ListTemplatesQuery",
    "TemplateCreated",
    "TemplatePage",
    "TemplateWithVersions",
    "UpdateDraftFieldsCommand",
]
