"""tenant: departments and department_memberships tables

Revision ID: 0003_tenant_departments
Revises: 0002_tenant_tasks
Create Date: 2026-07-02

Runs inside the current tenant schema (search_path is set by env.py).
No schema= here on purpose — keeps the migration replayable for every new
tenant.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_tenant_departments"
down_revision: str | Sequence[str] | None = "0002_tenant_tasks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "departments",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(length=200), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
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
        sa.CheckConstraint("char_length(name) >= 1", name="departments_name_nonempty_chk"),
    )

    op.create_table(
        "department_memberships",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "department_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(
            ["department_id"],
            ["departments.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_department_memberships_dept_sub",
        "department_memberships",
        ["department_id", "subject"],
        unique=True,
    )
    op.create_index(
        "ix_department_memberships_subject",
        "department_memberships",
        ["subject"],
    )


def downgrade() -> None:
    op.drop_index("ix_department_memberships_subject", table_name="department_memberships")
    op.drop_index("ix_department_memberships_dept_sub", table_name="department_memberships")
    op.drop_table("department_memberships")
    op.drop_table("departments")
