"""SQLAlchemy-backed adapter for TemplateRepository port.

Callers own the transaction boundary (SessionDep); this adapter ``flush()``s
but never ``commit()``s.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.intake_templates.domain.entities import (
    FieldDefinition,
    FieldKind,
    Template,
    TemplateType,
    TemplateVersion,
    TemplateVersionStatus,
)
from app.modules.intake_templates.domain.errors import (
    DuplicateTemplateNameError,
    TemplateVersionNotFoundError,
)
from app.modules.intake_templates.infrastructure.models import (
    TemplateRow,
    TemplateVersionRow,
)

# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _fields_to_json(fields: tuple[FieldDefinition, ...]) -> list[dict[str, Any]]:
    return [
        {
            "key": f.key,
            "label": f.label,
            "kind": f.kind.value,
            "required": f.required,
            "config": f.config,
        }
        for f in fields
    ]


def _fields_from_json(raw: list[Any]) -> tuple[FieldDefinition, ...]:
    return tuple(
        FieldDefinition(
            key=item["key"],
            label=item["label"],
            kind=FieldKind(item["kind"]),
            required=item["required"],
            config=dict(item.get("config") or {}),
        )
        for item in raw
    )


# ---------------------------------------------------------------------------
# Row → entity
# ---------------------------------------------------------------------------


def _to_template(row: TemplateRow) -> Template:
    return Template(
        id=row.id,
        type=TemplateType(row.type),
        name=row.name,
        description=row.description,
        is_system=row.is_system,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_version(row: TemplateVersionRow) -> TemplateVersion:
    row_fields: Any = row.fields
    raw_fields: list[Any] = row_fields if isinstance(row_fields, list) else []
    return TemplateVersion(
        id=row.id,
        template_id=row.template_id,
        version_number=row.version_number,
        status=TemplateVersionStatus(row.status),
        fields=_fields_from_json(raw_fields),
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


class SqlAlchemyTemplateRepository:
    """Concrete TemplateRepository on AsyncSession. Caller owns the transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Template operations
    # ------------------------------------------------------------------

    async def add_template(self, template: Template) -> None:
        row = TemplateRow(
            id=template.id,
            type=template.type.value,
            name=template.name,
            description=template.description,
            is_system=template.is_system,
            created_at=template.created_at,
            updated_at=template.updated_at,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise DuplicateTemplateNameError(
                f"A template named '{template.name}' already exists in this tenant"
            ) from exc

    async def get_template_by_id(self, template_id: UUID) -> Template | None:
        row = await self._session.get(TemplateRow, template_id)
        return _to_template(row) if row is not None else None

    async def get_template_by_name(self, name: str) -> Template | None:
        row = (
            await self._session.scalars(select(TemplateRow).where(TemplateRow.name == name))
        ).first()
        return _to_template(row) if row is not None else None

    async def list_templates(self, *, limit: int, offset: int) -> tuple[list[Template], int]:
        base = select(TemplateRow)
        count_q = select(func.count()).select_from(TemplateRow)
        rows = (
            await self._session.scalars(base.order_by(TemplateRow.name).limit(limit).offset(offset))
        ).all()
        total = await self._session.scalar(count_q) or 0
        return [_to_template(r) for r in rows], total

    # ------------------------------------------------------------------
    # Version operations
    # ------------------------------------------------------------------

    async def add_version(self, version: TemplateVersion) -> None:
        row = TemplateVersionRow(
            id=version.id,
            template_id=version.template_id,
            version_number=version.version_number,
            status=version.status.value,
            fields=_fields_to_json(version.fields),
            created_by=version.created_by,
            created_at=version.created_at,
            updated_at=version.updated_at,
        )
        self._session.add(row)
        await self._session.flush()

    async def get_version_by_id(self, version_id: UUID) -> TemplateVersion | None:
        row = await self._session.get(TemplateVersionRow, version_id)
        return _to_version(row) if row is not None else None

    async def get_latest_published_version(self, template_id: UUID) -> TemplateVersion | None:
        row = (
            await self._session.scalars(
                select(TemplateVersionRow)
                .where(
                    TemplateVersionRow.template_id == template_id,
                    TemplateVersionRow.status == TemplateVersionStatus.PUBLISHED.value,
                )
                .order_by(TemplateVersionRow.version_number.desc())
                .limit(1)
            )
        ).first()
        return _to_version(row) if row is not None else None

    async def list_versions(self, template_id: UUID) -> list[TemplateVersion]:
        rows = (
            await self._session.scalars(
                select(TemplateVersionRow)
                .where(TemplateVersionRow.template_id == template_id)
                .order_by(TemplateVersionRow.version_number)
            )
        ).all()
        return [_to_version(r) for r in rows]

    async def update_version(self, version: TemplateVersion) -> TemplateVersion:
        row = await self._session.get(TemplateVersionRow, version.id)
        if row is None:  # should not happen — service loads before updating
            raise TemplateVersionNotFoundError(f"Version {version.id} not found")
        row.status = version.status.value
        row.fields = _fields_to_json(version.fields)
        row.updated_at = version.updated_at
        await self._session.flush()
        return version
