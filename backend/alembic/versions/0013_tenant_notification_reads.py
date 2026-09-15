"""tenant: notification_reads — per-item read marks

Revision ID: 0013_tenant_notification_reads
Revises: 0012_tenant_chat_analysis
Create Date: 2026-09-15

Runs inside the current tenant schema (search_path is set by env.py).
No schema= here on purpose — keeps the migration replayable for every new
tenant.

The notification feed itself has no table: it is derived from audit_entries on
read (PRODUCT_MODULES §5, pull model).  The only persisted state is one row per
(user, event) they have dismissed — per item rather than a single watermark,
because the bell must be able to clear ONE notification while older unread ones
stay unread.

No FK to audit_entries on purpose: that table is append-only (the migration
REVOKEs DELETE), so a dangling entry_id cannot arise, and the FK check would
cost an index probe on every insert.

No new index is needed for the feed query — 0008 already created
``ix_audit_entries_ticket_occurred`` (occurred_at DESC WHERE
entity_type = 'ticket'), which is the feed's driving scan, and the composite PK
below serves the per-subject LEFT JOIN.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_tenant_notification_reads"
down_revision: str | Sequence[str] | None = "0012_tenant_chat_analysis"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notification_reads",
        sa.Column("subject", sa.String(length=255), primary_key=True),
        sa.Column(
            "entry_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "read_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("notification_reads")
