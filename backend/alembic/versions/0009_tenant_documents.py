"""tenant: documents table (per-tenant document library)

Revision ID: 0009_tenant_documents
Revises: 0008_tenant_audit_ticket_idx
Create Date: 2026-08-18

Runs inside the current tenant schema (search_path is set by env.py). No
schema= here on purpose — keeps the migration replayable for every tenant.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0009_tenant_documents"
down_revision: str | Sequence[str] | None = "0008_tenant_audit_ticket_idx"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("owner_id", UUID(as_uuid=True), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="uploaded"
        ),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("extraction_error", sa.String(length=2_000), nullable=True),
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
            "status IN ('uploaded', 'extracted', 'failed')", name="documents_status_chk"
        ),
        sa.CheckConstraint("size_bytes > 0", name="documents_size_chk"),
        sa.CheckConstraint(
            "char_length(filename) BETWEEN 1 AND 255", name="documents_filename_len_chk"
        ),
    )
    op.create_index(
        "ix_documents_owner_created_at", "documents", ["owner_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_documents_owner_created_at", table_name="documents")
    op.drop_table("documents")
