"""tenant: ai_chat_sessions and ai_chat_messages tables

Revision ID: 0007_tenant_ai_chat
Revises: 0006_tenant_tickets
Create Date: 2026-07-07

Runs inside the current tenant schema (search_path is set by env.py).
The chat history IS the per-session LLM context store — durable, resumable,
tenant-isolated.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0007_tenant_ai_chat"
down_revision: str | Sequence[str] | None = "0006_tenant_tickets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_chat_sessions",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("requester_id", UUID(as_uuid=True), nullable=False),
        sa.Column("requester_sub", sa.String(length=255), nullable=False),
        sa.Column("template_version_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="active"
        ),
        sa.Column(
            "draft", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("draft_title", sa.String(length=200), nullable=True),
        sa.Column(
            "message_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("ticket_id", UUID(as_uuid=True), nullable=True),
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
            "status IN ('active', 'submitted', 'discarded')",
            name="ai_chat_sessions_status_chk",
        ),
        sa.CheckConstraint(
            "status != 'submitted' OR ticket_id IS NOT NULL",
            name="ai_chat_sessions_submitted_ticket_chk",
        ),
        sa.CheckConstraint("message_count >= 0", name="ai_chat_sessions_count_chk"),
    )
    op.create_index(
        "ix_ai_chat_sessions_requester",
        "ai_chat_sessions",
        ["requester_id", "created_at"],
    )
    op.create_index("ix_ai_chat_sessions_status", "ai_chat_sessions", ["status"])

    op.create_table(
        "ai_chat_messages",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("session_id", UUID(as_uuid=True), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=10), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "role IN ('user', 'assistant')", name="ai_chat_messages_role_chk"
        ),
        sa.CheckConstraint("seq >= 1", name="ai_chat_messages_seq_chk"),
        sa.ForeignKeyConstraint(
            ["session_id"], ["ai_chat_sessions.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("session_id", "seq", name="ai_chat_messages_session_seq_uq"),
    )


def downgrade() -> None:
    op.drop_table("ai_chat_messages")
    op.drop_index("ix_ai_chat_sessions_status", table_name="ai_chat_sessions")
    op.drop_index("ix_ai_chat_sessions_requester", table_name="ai_chat_sessions")
    op.drop_table("ai_chat_sessions")
