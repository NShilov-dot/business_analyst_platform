"""tenant: audit_entries table (append-only audit log)

Revision ID: 0005_tenant_audit
Revises: 0004_tenant_intake_templates
Create Date: 2026-07-04

Runs inside the current tenant schema (search_path is set by env.py).
No schema= here on purpose — keeps the migration replayable for every new
tenant.

SECURITY
--------
The migration REVOKEs UPDATE and DELETE from the application role (`app`)
so no application code can silently overwrite or remove audit rows.

Caveats:
- The database OWNER role can technically bypass these grants — this is
  defense-in-depth, not a hard security guarantee.
- Real enforcement is at the grants-provisioning level (Phase 2 hardening):
  the provisioning saga should also REVOKE from the owner-equivalent roles
  or use a dedicated audit-writer role.
- See PRODUCT_MODULES §8 open item 7 for the outstanding legal/retention
  question on PII vs append-only rows.

INDEXES
-------
- (entity_type, entity_id, occurred_at)  — entity-level history queries.
- (occurred_at)                           — time-range sweeps and admin views.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_tenant_audit"
down_revision: str | Sequence[str] | None = "0004_tenant_intake_templates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_entries",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("entity_type", sa.String(length=100), nullable=False),
        sa.Column(
            "entity_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("actor", sa.String(length=255), nullable=False),
        sa.Column(
            "roles",
            sa.dialects.postgresql.JSONB(),
            nullable=True,
        ),
        sa.Column(
            "before",
            sa.dialects.postgresql.JSONB(),
            nullable=True,
        ),
        sa.Column(
            "after",
            sa.dialects.postgresql.JSONB(),
            nullable=True,
        ),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_index(
        "ix_audit_entries_entity_type_id_occurred",
        "audit_entries",
        ["entity_type", "entity_id", "occurred_at"],
    )
    op.create_index(
        "ix_audit_entries_occurred_at",
        "audit_entries",
        ["occurred_at"],
    )

    # INSERT-only enforcement: revoke UPDATE, DELETE, and TRUNCATE from the
    # runtime application role.  We use current_user so the REVOKE always
    # applies to whichever role actually runs the migration — no hardcoded
    # role name, no silent skip.  Owner can bypass — see module docstring.
    op.execute(
        sa.text(
            "REVOKE UPDATE, DELETE, TRUNCATE ON audit_entries FROM current_user;"
        )
    )


def downgrade() -> None:
    # NOTE: we intentionally do NOT restore UPDATE/DELETE/TRUNCATE grants here.
    # The table is dropped, so there is nothing to grant on.  A subsequent
    # upgrade() will re-apply the REVOKE on the newly created table.
    op.drop_index("ix_audit_entries_occurred_at", table_name="audit_entries")
    op.drop_index("ix_audit_entries_entity_type_id_occurred", table_name="audit_entries")
    op.drop_table("audit_entries")
