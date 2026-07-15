"""tenant: tasks table

Revision ID: 0002_tenant_tasks
Revises: 0001_tenant_init
Create Date: 2026-06-16

Runs inside the current tenant schema (search_path is set by env.py).
No schema= here on purpose — keeps the migration replayable for every new
tenant.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_tenant_tasks"
down_revision: str | Sequence[str] | None = "0001_tenant_init"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')
    op.create_table(
        "tasks",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "owner_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'open'"),
        ),
        sa.Column(
            "priority",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'medium'"),
        ),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('open', 'in_progress', 'done', 'cancelled')",
            name="tasks_status_chk",
        ),
        sa.CheckConstraint(
            "priority IN ('low', 'medium', 'high')", name="tasks_priority_chk"
        ),
        sa.CheckConstraint(
            "char_length(title) BETWEEN 1 AND 200", name="tasks_title_len_chk"
        ),
    )
    op.create_index("ix_tasks_owner_status", "tasks", ["owner_id", "status"])
    op.create_index(
        "ix_tasks_owner_created_at", "tasks", ["owner_id", "created_at"]
    )
    op.create_index("ix_tasks_due_at", "tasks", ["due_at"])


def downgrade() -> None:
    op.drop_index("ix_tasks_due_at", table_name="tasks")
    op.drop_index("ix_tasks_owner_created_at", table_name="tasks")
    op.drop_index("ix_tasks_owner_status", table_name="tasks")
    op.drop_table("tasks")
