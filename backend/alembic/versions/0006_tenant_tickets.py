"""tenant: tickets workflow tables

Revision ID: 0006_tenant_tickets
Revises: 0005_tenant_audit
Create Date: 2026-07-06

Runs inside the current tenant schema (search_path is set by env.py).
No schema= here on purpose — keeps the migration replayable for every new
tenant.

Tables (mirroring modules/tickets/infrastructure/models.py):
- tickets                    — aggregate root, 8-value workflow status
- ticket_status_transitions  — append-only transition log (from NULL = creation)
- ticket_intake_submissions  — append-only intake payload versions
- ticket_triage_decisions    — append-only triage decision records
- ticket_assignments         — role holders; one ACTIVE row per (ticket, role)
                               via partial unique index
- ticket_gate_attestations   — immutable gate signatures; unique per
                               (ticket, kind, cycle)
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0006_tenant_tickets"
down_revision: str | Sequence[str] | None = "0005_tenant_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUSES = (
    "'created', 'triage', 'spec_approval', 'in_progress',"
    " 'change_capture', 'acceptance', 'closed', 'rejected'"
)


def upgrade() -> None:
    op.create_table(
        "tickets",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="created"
        ),
        sa.Column("author_id", UUID(as_uuid=True), nullable=False),
        sa.Column("department_id", UUID(as_uuid=True), nullable=True),
        sa.Column("priority", sa.String(length=10), nullable=True),
        sa.Column(
            "acceptance_cycle", sa.Integer(), nullable=False, server_default=sa.text("1")
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
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(f"status IN ({_STATUSES})", name="tickets_status_chk"),
        sa.CheckConstraint(
            "priority IS NULL OR priority IN ('low', 'medium', 'high', 'critical')",
            name="tickets_priority_chk",
        ),
        sa.CheckConstraint(
            "char_length(title) BETWEEN 1 AND 200", name="tickets_title_len_chk"
        ),
        sa.CheckConstraint("acceptance_cycle >= 1", name="tickets_cycle_chk"),
    )
    op.create_index("ix_tickets_status", "tickets", ["status"])
    op.create_index("ix_tickets_department_id", "tickets", ["department_id"])
    op.create_index("ix_tickets_author_created_at", "tickets", ["author_id", "created_at"])

    op.create_table(
        "ticket_status_transitions",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("ticket_id", UUID(as_uuid=True), nullable=False),
        sa.Column("from_status", sa.String(length=20), nullable=True),
        sa.Column("to_status", sa.String(length=20), nullable=False),
        sa.Column("actor", sa.String(length=255), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("acceptance_cycle", sa.Integer(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            f"from_status IS NULL OR from_status IN ({_STATUSES})",
            name="ticket_transitions_from_chk",
        ),
        sa.CheckConstraint(
            f"to_status IN ({_STATUSES})", name="ticket_transitions_to_chk"
        ),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_ticket_transitions_ticket_occurred",
        "ticket_status_transitions",
        ["ticket_id", "occurred_at"],
    )

    op.create_table(
        "ticket_intake_submissions",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("ticket_id", UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("template_version_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "payload", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("version >= 1", name="ticket_submissions_version_chk"),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "ticket_id", "version", name="ticket_submissions_ticket_ver_uq"
        ),
    )
    op.create_index(
        "ix_ticket_submissions_template_version",
        "ticket_intake_submissions",
        ["template_version_id"],
    )

    op.create_table(
        "ticket_triage_decisions",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("ticket_id", UUID(as_uuid=True), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("department_id", UUID(as_uuid=True), nullable=True),
        sa.Column("rejection_reason", sa.String(length=20), nullable=True),
        sa.Column("duplicate_of_ticket_id", UUID(as_uuid=True), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("decided_by", sa.String(length=255), nullable=False),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "outcome IN ('accepted', 'returned', 'rejected')",
            name="ticket_triage_outcome_chk",
        ),
        sa.CheckConstraint(
            "rejection_reason IS NULL"
            " OR rejection_reason IN ('duplicate', 'irrelevant', 'unjustified')",
            name="ticket_triage_reason_chk",
        ),
        sa.CheckConstraint(
            "outcome != 'accepted' OR department_id IS NOT NULL",
            name="ticket_triage_accept_dept_chk",
        ),
        sa.CheckConstraint(
            "outcome != 'rejected' OR rejection_reason IS NOT NULL",
            name="ticket_triage_reject_reason_chk",
        ),
        sa.CheckConstraint(
            "rejection_reason IS DISTINCT FROM 'duplicate'"
            " OR duplicate_of_ticket_id IS NOT NULL",
            name="ticket_triage_duplicate_ref_chk",
        ),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_ticket_triage_ticket_decided",
        "ticket_triage_decisions",
        ["ticket_id", "decided_at"],
    )

    op.create_table(
        "ticket_assignments",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("ticket_id", UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("assigned_by", sa.String(length=255), nullable=False),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("unassigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "role IN ('business_owner', 'executor')", name="ticket_assignments_role_chk"
        ),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "uq_ticket_assignments_active",
        "ticket_assignments",
        ["ticket_id", "role"],
        unique=True,
        postgresql_where=sa.text("unassigned_at IS NULL"),
    )
    op.create_index("ix_ticket_assignments_subject", "ticket_assignments", ["subject"])

    op.create_table(
        "ticket_gate_attestations",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("ticket_id", UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("acceptance_cycle", sa.Integer(), nullable=False),
        sa.Column("attested_by", sa.String(length=255), nullable=False),
        sa.Column(
            "roles_snapshot",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("checklist", JSONB(), nullable=True),
        sa.Column("spec_ref", sa.String(length=500), nullable=True),
        sa.Column("agreed_with_subject", sa.String(length=255), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "attested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "kind IN ('spec_approved', 'formal_dod', 'business_value')",
            name="ticket_attestations_kind_chk",
        ),
        sa.CheckConstraint(
            "(kind = 'spec_approved' AND acceptance_cycle = 0)"
            " OR (kind IN ('formal_dod', 'business_value') AND acceptance_cycle >= 1)",
            name="ticket_attestations_cycle_chk",
        ),
        sa.CheckConstraint(
            "kind != 'spec_approved' OR spec_ref IS NOT NULL",
            name="ticket_attestations_spec_ref_chk",
        ),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "ticket_id",
            "kind",
            "acceptance_cycle",
            name="ticket_attestations_ticket_kind_cycle_uq",
        ),
    )


def downgrade() -> None:
    op.drop_table("ticket_gate_attestations")
    op.drop_index("ix_ticket_assignments_subject", table_name="ticket_assignments")
    op.drop_index("uq_ticket_assignments_active", table_name="ticket_assignments")
    op.drop_table("ticket_assignments")
    op.drop_index("ix_ticket_triage_ticket_decided", table_name="ticket_triage_decisions")
    op.drop_table("ticket_triage_decisions")
    op.drop_index(
        "ix_ticket_submissions_template_version", table_name="ticket_intake_submissions"
    )
    op.drop_table("ticket_intake_submissions")
    op.drop_index(
        "ix_ticket_transitions_ticket_occurred", table_name="ticket_status_transitions"
    )
    op.drop_table("ticket_status_transitions")
    op.drop_index("ix_tickets_author_created_at", table_name="tickets")
    op.drop_index("ix_tickets_department_id", table_name="tickets")
    op.drop_index("ix_tickets_status", table_name="tickets")
    op.drop_table("tickets")
