"""tenant: ai_chat_sessions.documents_prefilled_keys

Revision ID: 0011_tenant_prefilled_keys
Revises: 0010_tenant_session_documents
Create Date: 2026-08-18

Runs inside the current tenant schema (search_path is set by env.py). No
schema= here on purpose — keeps the migration replayable for every tenant.
Records which draft keys the documents pre-analysis seeded, so the draft panel
can mark them as "из документа".
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0011_tenant_prefilled_keys"
down_revision: str | Sequence[str] | None = "0010_tenant_session_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ai_chat_sessions",
        sa.Column(
            "documents_prefilled_keys",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("ai_chat_sessions", "documents_prefilled_keys")
