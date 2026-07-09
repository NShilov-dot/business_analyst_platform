"""Application-layer DTOs for the tickets module.

Plain Python dataclasses — no Pydantic / FastAPI / SQLAlchemy imports.
Nullable fields that can be explicitly set to None use ``*_set`` flags
(same convention as tasks / departments modules).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import NamedTuple
from uuid import UUID

from app.modules.tickets.domain.entities import (
    Assignment,
    AssignmentRole,
    GateAttestation,
    IntakeSubmission,
    RejectionReason,
    Ticket,
    TicketPriority,
    TicketStatus,
    TriageDecision,
)

# ---------------------------------------------------------------------------
# Ticket commands
# ---------------------------------------------------------------------------


@dataclass(slots=True, kw_only=True)
class CreateTicketCommand:
    """Create a ticket draft (status=created) with an initial submission v1."""

    title: str
    description: str | None = None
    priority: TicketPriority | None = None
    template_version_id: UUID = field(default_factory=lambda: __import__("uuid").UUID(
        "20000000-0000-0000-0000-000000000001"
    ))
    payload: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True, kw_only=True)
class UpdateTicketCommand:
    """Partial metadata update (only allowed in {created, triage}).

    For nullable fields, the ``*_set`` flag MUST be True to apply the value
    — distinguishes "leave alone" from "set to null".
    """

    title: str | None = None
    description: str | None = None
    description_set: bool = False
    priority: TicketPriority | None = None
    priority_set: bool = False


@dataclass(slots=True, kw_only=True)
class ReplaceSubmissionCommand:
    """Replace the intake submission with a new version (only in status=created)."""

    template_version_id: UUID
    payload: dict[str, object]


@dataclass(slots=True, kw_only=True)
class SubmitCommand:
    """Edge 1: created → triage (submit for triage review)."""
    # No extra fields — actor and ticket_id come from service method params.


@dataclass(slots=True, kw_only=True)
class TriageAcceptCommand:
    """Edge 2: triage → spec_approval.

    ``business_owner_subject`` is mandatory (must be assigned before this
    transition — guarantees a value-gate signer exists).
    ``executor_subject`` is optional at triage time.
    """

    department_id: UUID
    business_owner_subject: str  # Keycloak sub
    executor_subject: str | None = None
    priority: TicketPriority | None = None
    comment: str | None = None


@dataclass(slots=True, kw_only=True)
class ReturnForRefinementCommand:
    """Edge 3 [PO]: triage → created. Comment is mandatory."""

    comment: str


@dataclass(slots=True, kw_only=True)
class RejectCommand:
    """Edges 4 (triage → rejected) and 10 (acceptance → rejected).

    For triage rejection, ``rejection_reason`` is required.
    For acceptance rejection, ``comment`` is sufficient.
    ``duplicate_of_ticket_id`` is only required when reason == duplicate.
    """

    comment: str
    rejection_reason: RejectionReason | None = None
    duplicate_of_ticket_id: UUID | None = None


@dataclass(slots=True, kw_only=True)
class AssignCommand:
    """Assign or reassign a role on a ticket (history preserved)."""

    role: AssignmentRole
    subject: str  # Keycloak sub (new assignee)


@dataclass(slots=True, kw_only=True)
class AttestSpecApprovalCommand:
    """Record spec_approved attestation (kind=spec_approved, cycle=0).

    Only the BA role may sign this — no admin fallback.
    ``agreed_with_subject`` must equal the active business_owner.

    ``spec_ref`` is optional: in this product the ticket itself IS the ТЗ, so
    when it is omitted the service pins a self-reference to the ticket and its
    current intake-submission version. An explicit value (e.g. an external
    Confluence/tracker URL) is still honoured if provided.
    """

    agreed_with_subject: str  # must match active business_owner sub
    spec_ref: str | None = None
    comment: str | None = None
    roles_snapshot: list[str] = field(default_factory=list)


@dataclass(slots=True, kw_only=True)
class AttestFormalDodCommand:
    """Record formal_dod attestation (kind=formal_dod, cycle=current).

    Only the BA role may sign this — no admin fallback.
    All checklist items must be True.
    """

    checklist: dict[str, bool]  # all values must be True
    comment: str | None = None
    roles_snapshot: list[str] = field(default_factory=list)


@dataclass(slots=True, kw_only=True)
class AttestBusinessValueCommand:
    """Record business_value attestation (kind=business_value, cycle=current).

    ONLY the active business_owner subject may sign — no role fallback,
    no admin override.
    """

    comment: str | None = None
    roles_snapshot: list[str] = field(default_factory=list)


@dataclass(slots=True, kw_only=True)
class StartWorkCommand:
    """Edge 5: spec_approval → in_progress.

    Guards (SpecApprovalGate + executor assignment) are checked by the service.
    """
    # No extra fields.


@dataclass(slots=True, kw_only=True)
class FinishWorkCommand:
    """Edge 6: in_progress → change_capture."""
    # No extra fields.


@dataclass(slots=True, kw_only=True)
class RequestAcceptanceCommand:
    """Edge 7: change_capture → acceptance. ``changes_summary`` is mandatory."""

    changes_summary: str


@dataclass(slots=True, kw_only=True)
class ReturnToWorkCommand:
    """Edge 9 [PO]: acceptance → in_progress. Comment is mandatory."""

    comment: str


@dataclass(slots=True, kw_only=True)
class CloseCommand:
    """Edge 8: acceptance → closed.

    Guard (AcceptanceGate — both attestations for current cycle) is checked
    by the service.
    """
    # No extra fields.


# ---------------------------------------------------------------------------
# Query objects
# ---------------------------------------------------------------------------


@dataclass(slots=True, kw_only=True)
class ListTicketsQuery:
    status: TicketStatus | None = None
    department_id: UUID | None = None
    mine: bool = False        # filter to author == caller
    assigned_to_me: bool = False  # filter to active assignment == caller
    limit: int = 20
    offset: int = 0


@dataclass(slots=True, kw_only=True)
class IntakeShareQuery:
    created_from: datetime | None = None
    created_to: datetime | None = None


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


class TicketPage(NamedTuple):
    items: list[Ticket]
    total: int
    limit: int
    offset: int


@dataclass(slots=True, kw_only=True)
class TicketDetail:
    """Full ticket detail response: aggregate + related records."""

    ticket: Ticket
    current_submission: IntakeSubmission | None
    assignments: list[Assignment]
    attestations: list[GateAttestation]
    triage_decisions: list[TriageDecision]


@dataclass(slots=True, kw_only=True)
class IntakeShareRow:
    template_version_id: UUID
    count: int


@dataclass(slots=True, kw_only=True)
class IntakeShareStats:
    total: int
    free_form: int
    templated: int
    templated_share: float
    by_template_version: list[IntakeShareRow]
