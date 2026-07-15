"""Pydantic request/response schemas for the intake_templates API.

Each Request schema has a ``to_command()`` method; each Response schema has a
``from_entity()`` classmethod. ``Envelope[T]`` and ``PagedEnvelope[T]`` follow
the same pattern as other modules.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.intake_templates.application.dtos import (
    CreateTemplateCommand,
    TemplatePage,
    UpdateDraftFieldsCommand,
)
from app.modules.intake_templates.domain.entities import (
    FieldDefinition,
    FieldKind,
    Template,
    TemplateSelectionRule,
    TemplateType,
    TemplateVersion,
    TemplateVersionStatus,
)

T = TypeVar("T")

NAME_MAX = 200
DESCRIPTION_MAX = 4_000
LABEL_MAX = 200
KEY_MAX = 100


# ---------------------------------------------------------------------------
# Field-level schemas
# ---------------------------------------------------------------------------


class FieldDefinitionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=KEY_MAX)
    label: str = Field(min_length=1, max_length=LABEL_MAX)
    kind: FieldKind
    required: bool
    config: dict[str, Any] = Field(default_factory=dict)

    def to_domain(self) -> FieldDefinition:
        return FieldDefinition(
            key=self.key,
            label=self.label,
            kind=self.kind,
            required=self.required,
            config=self.config,
        )


class FieldDefinitionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    label: str
    kind: FieldKind
    required: bool
    config: dict[str, Any]

    @classmethod
    def from_entity(cls, fd: FieldDefinition) -> FieldDefinitionResponse:
        return cls(
            key=fd.key,
            label=fd.label,
            kind=fd.kind,
            required=fd.required,
            config=fd.config,
        )


# ---------------------------------------------------------------------------
# Template request schemas
# ---------------------------------------------------------------------------


class CreateTemplateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: TemplateType
    name: str = Field(min_length=1, max_length=NAME_MAX)
    description: str | None = Field(default=None, max_length=DESCRIPTION_MAX)

    @field_validator("name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("name must not be blank")
        return stripped

    def to_command(self) -> CreateTemplateCommand:
        return CreateTemplateCommand(
            template_type=self.type,
            name=self.name,
            description=self.description,
        )


class UpdateDraftFieldsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fields: list[FieldDefinitionInput] = Field(min_length=1)

    def to_command(self) -> UpdateDraftFieldsCommand:
        return UpdateDraftFieldsCommand(fields=tuple(f.to_domain() for f in self.fields))


# ---------------------------------------------------------------------------
# Template response schemas
# ---------------------------------------------------------------------------


class TemplateResponse(BaseModel):
    """Lightweight template summary used in the list endpoint."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    type: TemplateType
    name: str
    description: str | None
    is_system: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_entity(cls, template: Template) -> TemplateResponse:
        return cls(
            id=template.id,
            type=template.type,
            name=template.name,
            description=template.description,
            is_system=template.is_system,
            created_at=template.created_at,
            updated_at=template.updated_at,
        )


class TemplateVersionResponse(BaseModel):
    """Full version snapshot including the fields list."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    template_id: UUID
    version_number: int
    status: TemplateVersionStatus
    fields: list[FieldDefinitionResponse]
    created_by: UUID
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_entity(cls, version: TemplateVersion) -> TemplateVersionResponse:
        return cls(
            id=version.id,
            template_id=version.template_id,
            version_number=version.version_number,
            status=version.status,
            fields=[FieldDefinitionResponse.from_entity(f) for f in version.fields],
            created_by=version.created_by,
            created_at=version.created_at,
            updated_at=version.updated_at,
        )


class TemplateDetailResponse(BaseModel):
    """Full template with all versions — returned by create and get-by-id."""

    id: UUID
    type: TemplateType
    name: str
    description: str | None
    is_system: bool
    created_at: datetime
    updated_at: datetime
    versions: list[TemplateVersionResponse]

    @classmethod
    def from_entities(
        cls, template: Template, versions: list[TemplateVersion]
    ) -> TemplateDetailResponse:
        return cls(
            id=template.id,
            type=template.type,
            name=template.name,
            description=template.description,
            is_system=template.is_system,
            created_at=template.created_at,
            updated_at=template.updated_at,
            versions=[TemplateVersionResponse.from_entity(v) for v in versions],
        )


class SelectionRuleResponse(BaseModel):
    """One row of the §6.4 template selection table."""

    situation: str
    doc_template: str
    level: str
    intake_template_type: TemplateType

    @classmethod
    def from_entity(cls, rule: TemplateSelectionRule) -> SelectionRuleResponse:
        return cls(
            situation=rule.situation,
            doc_template=rule.doc_template,
            level=rule.level,
            intake_template_type=rule.intake_template_type,
        )


# ---------------------------------------------------------------------------
# Envelope wrappers (same pattern as other modules)
# ---------------------------------------------------------------------------


class PageMeta(BaseModel):
    total: int
    limit: int
    offset: int


class Envelope(BaseModel, Generic[T]):
    data: T


class PagedEnvelope(BaseModel, Generic[T]):
    data: list[T]
    meta: PageMeta

    @classmethod
    def from_page(cls, page: TemplatePage) -> PagedEnvelope[TemplateResponse]:
        return PagedEnvelope[TemplateResponse](
            data=[TemplateResponse.from_entity(t) for t in page.items],
            meta=PageMeta(total=page.total, limit=page.limit, offset=page.offset),
        )
