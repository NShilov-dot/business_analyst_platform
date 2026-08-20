"""tenant: ai_chat_sessions.documents_context + ai_chat_session_documents join

Revision ID: 0010_tenant_session_documents
Revises: 0009_tenant_documents
Create Date: 2026-08-18

Runs inside the current tenant schema (search_path is set by env.py). No
schema= here on purpose — keeps the migration replayable for every tenant.
Both ai_chat_sessions and documents live in the same tenant schema, so the
join table's FKs are valid despite crossing module boundaries.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0010_tenant_session_documents"
down_revision: str | Sequence[str] | None = "0009_tenant_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ai_chat_sessions", sa.Column("documents_context", sa.Text(), nullable=True)
    )
    op.create_table(
        "ai_chat_session_documents",
        sa.Column("session_id", UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["ai_chat_sessions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("session_id", "document_id"),
    )


def downgrade() -> None:
    op.drop_table("ai_chat_session_documents")
    op.drop_column("ai_chat_sessions", "documents_context")
