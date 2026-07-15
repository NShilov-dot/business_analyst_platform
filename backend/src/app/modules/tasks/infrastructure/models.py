from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class TaskRow(Base):
    """Tenant-scoped row. Lives in `tenant_<slug>` via current search_path —
    no `schema=` override here on purpose."""

    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'in_progress', 'done', 'cancelled')",
            name="tasks_status_chk",
        ),
        CheckConstraint("priority IN ('low', 'medium', 'high')", name="tasks_priority_chk"),
        CheckConstraint("char_length(title) BETWEEN 1 AND 200", name="tasks_title_len_chk"),
        Index("ix_tasks_owner_status", "owner_id", "status"),
        Index("ix_tasks_owner_created_at", "owner_id", "created_at"),
        Index("ix_tasks_due_at", "due_at"),
        {"info": {"tenant_scope": "tenant"}},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="open")
    priority: Mapped[str] = mapped_column(String(20), nullable=False, server_default="medium")
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
