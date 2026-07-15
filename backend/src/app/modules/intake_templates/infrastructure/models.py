"""SQLAlchemy ORM rows for the intake_templates module.

All tables are tenant-scoped: they live in ``tenant_<slug>`` via the per-request
``search_path`` set by ``session_for_tenant()``. No ``schema=`` override — that
is intentional so migrations are replayable for every new tenant.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

_TYPE_VALUES = "', '".join(
    [
        "feature_request",
        "change",
        "defect",
        "integration_data",
        "analytics_request",
        "free_form",
    ]
)
_STATUS_VALUES = "', '".join(["draft", "published", "archived"])


class TemplateRow(Base):
    """One row per template catalog entry within a tenant schema."""

    __tablename__ = "templates"
    __table_args__ = (
        CheckConstraint(
            f"type IN ('{_TYPE_VALUES}')",
            name="templates_type_chk",
        ),
        CheckConstraint("char_length(name) >= 1", name="templates_name_nonempty_chk"),
        Index("ix_templates_type", "type"),
        {"info": {"tenant_scope": "tenant"}},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    type: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class TemplateVersionRow(Base):
    """One row per versioned field schema snapshot for a template."""

    __tablename__ = "template_versions"
    __table_args__ = (
        CheckConstraint(
            f"status IN ('{_STATUS_VALUES}')",
            name="template_versions_status_chk",
        ),
        UniqueConstraint(
            "template_id",
            "version_number",
            name="template_versions_tmpl_ver_uq",
        ),
        Index("ix_template_versions_template_id", "template_id"),
        Index("ix_template_versions_status", "status"),
        {"info": {"tenant_scope": "tenant"}},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    template_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("templates.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    # JSONB list of FieldDefinition dicts:
    # [{"key": str, "label": str, "kind": str, "required": bool, "config": dict}, ...]
    fields: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    created_by: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
