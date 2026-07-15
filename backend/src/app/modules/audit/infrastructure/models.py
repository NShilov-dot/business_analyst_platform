"""SQLAlchemy ORM rows for the audit module.

The `audit_entries` table is INSERT-only by design:
- The migration (0005_tenant_audit.py) REVOKEs UPDATE and DELETE from the
  application role.  This is defense-in-depth; the database owner role can
  technically bypass it — see PRODUCT_MODULES §8 open item 7 for the
  outstanding legal/retention question on PII + append-only audit rows.
- No application code ever writes to this table directly: only the
  AuditEventSubscriber (via the EventBus) appends rows.
- All tables are tenant-scoped (no `schema=` override — relies on
  `search_path` set by `session_for_tenant()`).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class AuditEntryRow(Base):
    """Append-only audit log entry within a tenant schema."""

    __tablename__ = "audit_entries"
    __table_args__ = (
        # Composite index for entity-level history queries (most common access pattern).
        Index(
            "ix_audit_entries_entity_type_id_occurred",
            "entity_type",
            "entity_id",
            "occurred_at",
        ),
        # Chronological index for time-range sweeps and admin views.
        Index("ix_audit_entries_occurred_at", "occurred_at"),
        {"info": {"tenant_scope": "tenant"}},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    roles: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    request_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
