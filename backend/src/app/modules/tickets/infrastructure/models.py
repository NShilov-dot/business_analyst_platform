"""SQLAlchemy ORM rows for the tickets module.

All tables are tenant-scoped: they live in ``tenant_<slug>`` via the per-request
``search_path`` set by ``session_for_tenant()``. No ``schema=`` override — that
is intentional so migrations are replayable for every new tenant.

DB CHECK constraints mirror the domain invariants in ``domain/entities.py``
(defense-in-depth: the aggregate enforces them first, the DB enforces them
against any future writer that bypasses the domain).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

_STATUS_VALUES = "', '".join(
    [
        "created",
        "triage",
        "spec_approval",
        "in_progress",
        "change_capture",
        "acceptance",
        "closed",
        "rejected",
    ]
)
_PRIORITY_VALUES = "', '".join(["low", "medium", "high", "critical"])
_TRIAGE_OUTCOME_VALUES = "', '".join(["accepted", "returned", "rejected"])
_REJECTION_REASON_VALUES = "', '".join(["duplicate", "irrelevant", "unjustified"])
_ASSIGNMENT_ROLE_VALUES = "', '".join(["business_owner", "executor"])
_ATTESTATION_KIND_VALUES = "', '".join(["spec_approved", "formal_dod", "business_value"])


class TicketRow(Base):
    """Aggregate root row for the ticket workflow."""

    __tablename__ = "tickets"
    __table_args__ = (
        CheckConstraint(f"status IN ('{_STATUS_VALUES}')", name="tickets_status_chk"),
        CheckConstraint(
            f"priority IS NULL OR priority IN ('{_PRIORITY_VALUES}')",
            name="tickets_priority_chk",
        ),
        CheckConstraint(
            "char_length(title) BETWEEN 1 AND 200", name="tickets_title_len_chk"
        ),
        CheckConstraint("acceptance_cycle >= 1", name="tickets_cycle_chk"),
        Index("ix_tickets_status", "status"),
        Index("ix_tickets_department_id", "department_id"),
        Index("ix_tickets_author_created_at", "author_id", "created_at"),
        {"info": {"tenant_scope": "tenant"}},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="created")
    author_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    priority: Mapped[str | None] = mapped_column(String(10), nullable=True)
    acceptance_cycle: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TicketStatusTransitionRow(Base):
    """Append-only record of one workflow transition (from_status NULL = creation)."""

    __tablename__ = "ticket_status_transitions"
    __table_args__ = (
        CheckConstraint(
            f"from_status IS NULL OR from_status IN ('{_STATUS_VALUES}')",
            name="ticket_transitions_from_chk",
        ),
        CheckConstraint(
            f"to_status IN ('{_STATUS_VALUES}')", name="ticket_transitions_to_chk"
        ),
        Index("ix_ticket_transitions_ticket_occurred", "ticket_id", "occurred_at"),
        {"info": {"tenant_scope": "tenant"}},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False
    )
    from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    acceptance_cycle: Mapped[int] = mapped_column(Integer, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class IntakeSubmissionRow(Base):
    """Append-only snapshot of one version of the intake payload."""

    __tablename__ = "ticket_intake_submissions"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ticket_submissions_version_chk"),
        UniqueConstraint("ticket_id", "version", name="ticket_submissions_ticket_ver_uq"),
        Index("ix_ticket_submissions_template_version", "template_version_id"),
        {"info": {"tenant_scope": "tenant"}},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    template_version_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TriageDecisionRow(Base):
    """Append-only record of one triage decision."""

    __tablename__ = "ticket_triage_decisions"
    __table_args__ = (
        CheckConstraint(
            f"outcome IN ('{_TRIAGE_OUTCOME_VALUES}')",
            name="ticket_triage_outcome_chk",
        ),
        CheckConstraint(
            f"rejection_reason IS NULL OR rejection_reason IN ('{_REJECTION_REASON_VALUES}')",
            name="ticket_triage_reason_chk",
        ),
        # Mirror TriageDecision.__post_init__ invariants
        CheckConstraint(
            "outcome != 'accepted' OR department_id IS NOT NULL",
            name="ticket_triage_accept_dept_chk",
        ),
        CheckConstraint(
            "outcome != 'rejected' OR rejection_reason IS NOT NULL",
            name="ticket_triage_reject_reason_chk",
        ),
        CheckConstraint(
            "rejection_reason IS DISTINCT FROM 'duplicate'"
            " OR duplicate_of_ticket_id IS NOT NULL",
            name="ticket_triage_duplicate_ref_chk",
        ),
        Index("ix_ticket_triage_ticket_decided", "ticket_id", "decided_at"),
        {"info": {"tenant_scope": "tenant"}},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False
    )
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    rejection_reason: Mapped[str | None] = mapped_column(String(20), nullable=True)
    duplicate_of_ticket_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[str] = mapped_column(String(255), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AssignmentRow(Base):
    """Who holds a role on a ticket. Active = unassigned_at IS NULL.

    At most one ACTIVE assignment per (ticket, role) — enforced by the partial
    unique index created in the migration (``uq_ticket_assignments_active``).
    """

    __tablename__ = "ticket_assignments"
    __table_args__ = (
        CheckConstraint(
            f"role IN ('{_ASSIGNMENT_ROLE_VALUES}')", name="ticket_assignments_role_chk"
        ),
        Index(
            "uq_ticket_assignments_active",
            "ticket_id",
            "role",
            unique=True,
            postgresql_where=text("unassigned_at IS NULL"),
        ),
        Index("ix_ticket_assignments_subject", "subject"),
        {"info": {"tenant_scope": "tenant"}},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    assigned_by: Mapped[str] = mapped_column(String(255), nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    unassigned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class GateAttestationRow(Base):
    """Immutable attestation record satisfying a workflow gate."""

    __tablename__ = "ticket_gate_attestations"
    __table_args__ = (
        CheckConstraint(
            f"kind IN ('{_ATTESTATION_KIND_VALUES}')",
            name="ticket_attestations_kind_chk",
        ),
        # Cycle invariants: spec_approved pinned to 0; acceptance kinds >= 1
        CheckConstraint(
            "(kind = 'spec_approved' AND acceptance_cycle = 0)"
            " OR (kind IN ('formal_dod', 'business_value') AND acceptance_cycle >= 1)",
            name="ticket_attestations_cycle_chk",
        ),
        CheckConstraint(
            "kind != 'spec_approved' OR spec_ref IS NOT NULL",
            name="ticket_attestations_spec_ref_chk",
        ),
        UniqueConstraint(
            "ticket_id",
            "kind",
            "acceptance_cycle",
            name="ticket_attestations_ticket_kind_cycle_uq",
        ),
        {"info": {"tenant_scope": "tenant"}},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    acceptance_cycle: Mapped[int] = mapped_column(Integer, nullable=False)
    attested_by: Mapped[str] = mapped_column(String(255), nullable=False)
    roles_snapshot: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    checklist: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    spec_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    agreed_with_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    attested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
