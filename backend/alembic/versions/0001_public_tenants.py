"""public: tenants registry

Revision ID: 0001_public_tenants
Revises:
Create Date: 2026-06-16

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_public_tenants"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = ("public",)
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')
    op.create_table(
        "tenants",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("slug", sa.String(length=40), nullable=False, unique=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default=sa.text("'active'")
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
        sa.CheckConstraint(
            "status IN ('active', 'suspended', 'archived')", name="tenants_status_chk"
        ),
        sa.CheckConstraint(
            "slug ~ '^[a-z][a-z0-9_]{1,40}$'", name="tenants_slug_format_chk"
        ),
        schema="public",
    )
    op.create_index(
        "ix_public_tenants_slug", "tenants", ["slug"], unique=True, schema="public"
    )


def downgrade() -> None:
    op.drop_index("ix_public_tenants_slug", table_name="tenants", schema="public")
    op.drop_table("tenants", schema="public")
