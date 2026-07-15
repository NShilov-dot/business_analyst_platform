"""public: add keycloak_group_id to tenants

Revision ID: 0002_public_keycloak_group_id
Revises: 0001_public_tenants
Create Date: 2026-06-16

Stores the Keycloak group UUID that corresponds to this tenant.
Set by provision_tenant.py after the group is created via Keycloak Admin API.
Nullable to keep old rows valid and to allow environments where Keycloak Admin
is disabled.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_public_keycloak_group_id"
down_revision: str | Sequence[str] | None = "0001_public_tenants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("keycloak_group_id", sa.String(length=255), nullable=True),
        schema="public",
    )
    op.create_index(
        "ix_public_tenants_keycloak_group_id",
        "tenants",
        ["keycloak_group_id"],
        unique=True,
        schema="public",
        postgresql_where=sa.text("keycloak_group_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_public_tenants_keycloak_group_id",
        table_name="tenants",
        schema="public",
    )
    op.drop_column("tenants", "keycloak_group_id", schema="public")
