"""tenant: partial index on audit_entries for the ticket activity feed

Revision ID: 0008_tenant_audit_ticket_idx
Revises: 0007_tenant_ai_chat
Create Date: 2026-07-08

Runs inside the current tenant schema (search_path is set by env.py).
No schema= here on purpose — keeps the migration replayable for every new
tenant.

WHY
---
The analytics activity feed (GET /v1/analytics/activity) runs:

    SELECT ... FROM audit_entries
    WHERE entity_type = 'ticket'
    ORDER BY occurred_at DESC
    LIMIT :limit

The existing indexes cannot serve this efficiently: the composite
(entity_type, entity_id, occurred_at) index cannot satisfy the occurred_at
sort without also binding entity_id, and the (occurred_at) index does not
filter by entity_type. On a large audit log this degrades to a seq scan + sort.

A partial index on occurred_at DESC constrained to entity_type = 'ticket'
lets Postgres answer the feed with a bounded index scan.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_tenant_audit_ticket_idx"
down_revision: str | Sequence[str] | None = "0007_tenant_ai_chat"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "CREATE INDEX ix_audit_entries_ticket_occurred "
            "ON audit_entries (occurred_at DESC) "
            "WHERE entity_type = 'ticket'"
        )
    )


def downgrade() -> None:
    op.drop_index("ix_audit_entries_ticket_occurred", table_name="audit_entries")
