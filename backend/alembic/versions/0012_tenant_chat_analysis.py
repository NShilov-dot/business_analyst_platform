"""tenant: ai_chat_sessions.analysis_status

Revision ID: 0012_tenant_chat_analysis
Revises: 0011_tenant_prefilled_keys
Create Date: 2026-08-18

Runs inside the current tenant schema (search_path is set by env.py). No
schema= here on purpose — keeps the migration replayable for every tenant.
Tracks the lifecycle of the deferred, background document pre-analysis so the
frontend can poll GET /v1/intake-chat/sessions/{id} while the LLM pass runs
instead of blocking POST /v1/intake-chat/sessions on it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_tenant_chat_analysis"
down_revision: str | Sequence[str] | None = "0011_tenant_prefilled_keys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ai_chat_sessions",
        sa.Column(
            "analysis_status",
            sa.String(length=10),
            nullable=False,
            server_default="none",
        ),
    )
    op.create_check_constraint(
        "ai_chat_sessions_analysis_status_chk",
        "ai_chat_sessions",
        "analysis_status IN ('none', 'pending', 'ready', 'failed')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ai_chat_sessions_analysis_status_chk", "ai_chat_sessions", type_="check"
    )
    op.drop_column("ai_chat_sessions", "analysis_status")
