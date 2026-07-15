"""Tickets domain entities.

No SQLAlchemy / Pydantic / FastAPI imports allowed in this module.

Design notes:
- ALLOWED_TRANSITIONS is the single source of truth for valid (from, to) edges.
  Adding or removing a PO-confirmed edge = one line here, no migration needed.
- Every Ticket transition method:
    1. Checks ALLOWED_TRANSITIONS (raises TicketTransitionForbiddenError on failure).
    2. Applies the state change.
    3. Returns a StatusTransition value object that the caller persists and audits.
- Guards (SpecApprovalGate, AcceptanceGate, SubmissionValidator) are called by
  TicketService BEFORE the domain method — the aggregate never calls ports.
- acceptance_cycle: starts at 1; spec_approved attestation is pinned to cycle 0
  (DB CHECK cycle=0 for kind=spec_approved); acceptance attestations require cycle>=1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from app.modules.tickets.domain.errors import (
    AssignmentMissingError,
    TicketSubmissionFrozenError,
    TicketTransitionForbiddenError,
    TicketValidationError,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SYSTEM_TRACKER_ACTOR: str = "system:tracker"

# Edges that the system-actor (tracker webhook) is allowed to trigger.
# Only in_progress → change_capture in Phase 1 (spec §5).
SYSTEM_ACTOR_ALLOWED_EDGES: frozenset[tuple[str, str]] = frozenset(
    {("in_progress", "change_capture")}
)

TITLE_MAX = 200
DESCRIPTION_MAX = 4_000


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TicketStatus(StrEnum):
    """8-value workflow status (7 live + 1 terminal rejection branch)."""

    CREATED = "created"
    TRIAGE = "triage"
    SPEC_APPROVAL = "spec_approval"
    IN_PROGRESS = "in_progress"
    CHANGE_CAPTURE = "change_capture"
    ACCEPTANCE = "acceptance"
    CLOSED = "closed"
    REJECTED = "rejected"


TERMINAL_STATUSES: frozenset[TicketStatus] = frozenset(
    {TicketStatus.CLOSED, TicketStatus.REJECTED}
)

# Single data-driven edge table.  Adding / removing a PO-confirmed edge = one line.
# Key: from_status (None = creation), Value: set of allowed to_status values.
# Edge numbers from spec §3 transition table:
# 0: None → created (creation, not a transition in the strict sense)
# 1: created → triage (submit)
# 2: triage → spec_approval (triage accept)
# 3: triage → created [PO] (return-for-refinement)
# 4: triage → rejected (reject from triage)
# 5: spec_approval → in_progress (start-work)
# 6: in_progress → change_capture (finish-work)
# 7: change_capture → acceptance (request-acceptance)
# 8: acceptance → closed (close)
# 9: acceptance → in_progress [PO] (return-to-work)
# 10: acceptance → rejected [PO] (reject from acceptance)
ALLOWED_TRANSITIONS: dict[TicketStatus | None, frozenset[TicketStatus]] = {
    None: frozenset({TicketStatus.CREATED}),
    TicketStatus.CREATED: frozenset({TicketStatus.TRIAGE}),
    TicketStatus.TRIAGE: frozenset(
        {TicketStatus.SPEC_APPROVAL, TicketStatus.CREATED, TicketStatus.REJECTED}
    ),
    TicketStatus.SPEC_APPROVAL: frozenset({TicketStatus.IN_PROGRESS}),
    TicketStatus.IN_PROGRESS: frozenset({TicketStatus.CHANGE_CAPTURE}),
    TicketStatus.CHANGE_CAPTURE: frozenset({TicketStatus.ACCEPTANCE}),
    TicketStatus.ACCEPTANCE: frozenset(
        {TicketStatus.CLOSED, TicketStatus.IN_PROGRESS, TicketStatus.REJECTED}
    ),
    TicketStatus.CLOSED: frozenset(),
    TicketStatus.REJECTED: frozenset(),
}


class TicketPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class TriageOutcome(StrEnum):
    ACCEPTED = "accepted"
    RETURNED = "returned"
    REJECTED = "rejected"


class RejectionReason(StrEnum):
    DUPLICATE = "duplicate"
    IRRELEVANT = "irrelevant"
    UNJUSTIFIED = "unjustified"


class AssignmentRole(StrEnum):
    BUSINESS_OWNER = "business_owner"
    EXECUTOR = "executor"


class AttestationKind(StrEnum):
    SPEC_APPROVED = "spec_approved"    # cycle 0; only BA
    FORMAL_DOD = "formal_dod"          # cycle >= 1; only BA
    BUSINESS_VALUE = "business_value"  # cycle >= 1; active business_owner only


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GateCheckResult:
    """Result of a gate query (SpecApprovalGate or AcceptanceGate).

    ``ok=True`` means the gate is satisfied and the transition is allowed.
    ``reasons`` contains human-readable strings explaining why the gate
    failed — carried into error ``details`` when ``ok=False``.
    """

    ok: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def passed(cls) -> GateCheckResult:
        return cls(ok=True, reasons=())

    @classmethod
    def failed(cls, *reasons: str) -> GateCheckResult:
        return cls(ok=False, reasons=reasons)


@dataclass(frozen=True, slots=True)
class OpenDefectsResult:
    """Result of TrackerPort.open_defects_query.

    ``open_p0`` and ``open_p1`` are ``None`` when the tracker is unavailable
    or the result is unknown — NEVER use 0 as "clean" sentinel; only an
    explicit integer 0 means "confirmed zero open defects".
    ``checked_at`` is the UTC moment the tracker was queried.
    """

    open_p0: int | None
    open_p1: int | None
    checked_at: datetime


# ---------------------------------------------------------------------------
# StatusTransition — value object / record returned by every transition method
# ---------------------------------------------------------------------------


@dataclass(slots=True, kw_only=True)
class StatusTransition:
    """Immutable record of one workflow transition (or creation).

    One row is created per HTTP mutation; the repository persists it
    append-only.  ``from_status=None`` marks the initial creation row.
    """

    id: UUID
    ticket_id: UUID
    from_status: TicketStatus | None
    to_status: TicketStatus
    actor: str  # Keycloak sub or SYSTEM_TRACKER_ACTOR
    comment: str | None
    acceptance_cycle: int
    occurred_at: datetime


# ---------------------------------------------------------------------------
# IntakeSubmission — snapshot, append-only (frozen at status != created)
# ---------------------------------------------------------------------------


@dataclass(slots=True, kw_only=True)
class IntakeSubmission:
    """Immutable snapshot of one version of the intake payload.

    ``version`` is a per-ticket counter (1, 2, …); the current submission is
    the one with the highest version number.
    ``template_version_id`` must be PUBLISHED at the time of creation;
    the TemplateVersionInfo port enforces this in TicketService.
    """

    id: UUID
    ticket_id: UUID
    version: int
    template_version_id: UUID
    payload: dict[str, object]
    created_by: str  # Keycloak sub
    created_at: datetime


# ---------------------------------------------------------------------------
# TriageDecision — append-only decision record
# ---------------------------------------------------------------------------


@dataclass(slots=True, kw_only=True)
class TriageDecision:
    """Immutable record of one triage decision.

    Domain invariants (mirroring DB CHECK):
    - outcome=accepted ⇒ department_id NOT NULL
    - outcome=rejected ⇒ rejection_reason NOT NULL
    - outcome=rejected, rejection_reason=duplicate ⇒ duplicate_of_ticket_id NOT NULL
    """

    id: UUID
    ticket_id: UUID
    outcome: TriageOutcome
    department_id: UUID | None
    rejection_reason: RejectionReason | None
    duplicate_of_ticket_id: UUID | None
    comment: str | None
    decided_by: str  # Keycloak sub
    decided_at: datetime

    def __post_init__(self) -> None:
        if self.outcome == TriageOutcome.ACCEPTED and self.department_id is None:
            raise AssignmentMissingError(
                "Triage accept requires department_id"
            )
        if self.outcome == TriageOutcome.REJECTED and self.rejection_reason is None:
            raise AssignmentMissingError(
                "Triage reject requires rejection_reason"
            )
        if (
            self.outcome == TriageOutcome.REJECTED
            and self.rejection_reason == RejectionReason.DUPLICATE
            and self.duplicate_of_ticket_id is None
        ):
            raise AssignmentMissingError(
                "Rejection reason 'duplicate' requires duplicate_of_ticket_id"
            )


# ---------------------------------------------------------------------------
# Assignment — active = unassigned_at is None
# ---------------------------------------------------------------------------


@dataclass(slots=True, kw_only=True)
class Assignment:
    """Tracks who holds a role (business_owner or executor) on a ticket.

    At most one active assignment per (ticket, role) enforced by a partial
    unique index in the DB.  Replacing = closing old row + inserting new row
    (history preserved in the table).
    """

    id: UUID
    ticket_id: UUID
    role: AssignmentRole
    subject: str  # Keycloak sub
    assigned_by: str  # Keycloak sub
    assigned_at: datetime
    unassigned_at: datetime | None = field(default=None)

    @property
    def is_active(self) -> bool:
        return self.unassigned_at is None


# ---------------------------------------------------------------------------
# GateAttestation — immutable after signing
# ---------------------------------------------------------------------------


@dataclass(slots=True, kw_only=True)
class GateAttestation:
    """Immutable record written by a human actor to satisfy a gate.

    Cycle invariants (mirroring DB CHECK):
    - kind=spec_approved ⇒ acceptance_cycle == 0
    - kind in {formal_dod, business_value} ⇒ acceptance_cycle >= 1

    ``checklist`` is only present for kind=formal_dod (all values must be True).
    ``spec_ref`` is required for kind=spec_approved.
    """

    id: UUID
    ticket_id: UUID
    kind: AttestationKind
    acceptance_cycle: int
    attested_by: str  # Keycloak sub
    roles_snapshot: list[str]
    checklist: dict[str, bool] | None  # formal_dod only
    spec_ref: str | None              # spec_approved only (required)
    agreed_with_subject: str | None   # spec_approved: active business_owner sub
    comment: str | None
    attested_at: datetime

    def __post_init__(self) -> None:
        if self.kind == AttestationKind.SPEC_APPROVED and self.acceptance_cycle != 0:
            raise TicketValidationError(
                "spec_approved attestation must have acceptance_cycle == 0"
            )
        if (
            self.kind in {AttestationKind.FORMAL_DOD, AttestationKind.BUSINESS_VALUE}
            and self.acceptance_cycle < 1
        ):
            raise TicketValidationError(
                f"{self.kind} attestation requires acceptance_cycle >= 1"
            )
        if self.kind == AttestationKind.SPEC_APPROVED and not self.spec_ref:
            raise TicketValidationError(
                "spec_approved attestation requires spec_ref"
            )


# ---------------------------------------------------------------------------
# Ticket — aggregate root
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _validate_title(title: str) -> str:
    title = title.strip()
    if not title:
        raise TicketValidationError("Ticket title cannot be empty")
    if len(title) > TITLE_MAX:
        raise TicketValidationError(f"Ticket title exceeds {TITLE_MAX} characters")
    return title


def _validate_description(description: str | None) -> str | None:
    if description is None:
        return None
    if len(description) > DESCRIPTION_MAX:
        raise TicketValidationError(
            f"Ticket description exceeds {DESCRIPTION_MAX} characters"
        )
    return description


@dataclass(slots=True, kw_only=True)
class Ticket:
    """Aggregate root for the ticket workflow.

    State transitions are only allowed through the named methods.
    Every method returns a StatusTransition that the caller persists
    and audits — the aggregate itself does NOT write to the DB.

    ``acceptance_cycle`` starts at 1 and increments on each return-to-work.
    ``spec_approved`` attestation is pinned to cycle 0 and survives rework.
    """

    id: UUID
    title: str
    description: str | None
    status: TicketStatus
    author_id: UUID
    department_id: UUID | None
    priority: TicketPriority | None
    acceptance_cycle: int  # starts at 1
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None

    # ------------------------------------------------------------------ #
    # Factory                                                              #
    # ------------------------------------------------------------------ #

    @classmethod
    def create(
        cls,
        *,
        author_id: UUID,
        title: str,
        description: str | None = None,
        priority: TicketPriority | None = None,
        now: datetime | None = None,
    ) -> Ticket:
        ts = now or _now()
        return cls(
            id=uuid4(),
            title=_validate_title(title),
            description=_validate_description(description),
            status=TicketStatus.CREATED,
            author_id=author_id,
            department_id=None,
            priority=priority,
            acceptance_cycle=1,
            created_at=ts,
            updated_at=ts,
            closed_at=None,
        )

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _check_transition(
        self, to: TicketStatus, *, actor: str, comment: str | None, now: datetime
    ) -> StatusTransition:
        """Validate edge and return a StatusTransition; raise on failure."""
        allowed = ALLOWED_TRANSITIONS.get(self.status, frozenset())
        if to not in allowed:
            raise TicketTransitionForbiddenError(
                f"Transition {self.status!s} → {to!s} is not allowed"
            )
        return StatusTransition(
            id=uuid4(),
            ticket_id=self.id,
            from_status=self.status,
            to_status=to,
            actor=actor,
            comment=comment,
            acceptance_cycle=self.acceptance_cycle,
            occurred_at=now,
        )

    def _apply_transition(self, to: TicketStatus, *, now: datetime) -> None:
        self.status = to
        self.updated_at = now
        if to in TERMINAL_STATUSES:
            self.closed_at = now

    # ------------------------------------------------------------------ #
    # Metadata edits (only in {created, triage})                          #
    # ------------------------------------------------------------------ #

    def update_details(
        self,
        *,
        title: str | None = None,
        description: str | None = None,
        set_description: bool = False,
        priority: TicketPriority | None = None,
        set_priority: bool = False,
        now: datetime | None = None,
    ) -> None:
        """Partial update of mutable metadata.

        Only allowed in {created, triage}.  Raises TicketTransitionForbiddenError
        if the ticket has moved past the triage stage.
        """
        if self.status not in {TicketStatus.CREATED, TicketStatus.TRIAGE}:
            raise TicketTransitionForbiddenError(
                f"Cannot edit ticket details in status {self.status!s}"
            )
        if title is not None:
            self.title = _validate_title(title)
        if set_description:
            self.description = _validate_description(description)
        if set_priority:
            self.priority = priority
        self.updated_at = now or _now()

    def assert_submission_editable(self) -> None:
        """Raise TicketSubmissionFrozenError if new submission versions are not allowed."""
        if self.status != TicketStatus.CREATED:
            raise TicketSubmissionFrozenError(
                f"Intake submission is frozen in status {self.status!s}; "
                "new versions are only allowed while status == created"
            )

    # ------------------------------------------------------------------ #
    # Transition methods                                                   #
    # ------------------------------------------------------------------ #

    def submit(
        self,
        *,
        actor: str,
        now: datetime | None = None,
    ) -> StatusTransition:
        """Edge 1: created → triage.

        Guard (SubmissionValidator) is called by TicketService BEFORE this method.
        """
        ts = now or _now()
        transition = self._check_transition(TicketStatus.TRIAGE, actor=actor, comment=None, now=ts)
        self._apply_transition(TicketStatus.TRIAGE, now=ts)
        return transition

    def accept_triage(
        self,
        *,
        actor: str,
        department_id: UUID,
        now: datetime | None = None,
    ) -> StatusTransition:
        """Edge 2: triage → spec_approval.

        Sets ``department_id`` on the aggregate; requires business_owner assignment
        (checked by TicketService before calling this method).
        """
        ts = now or _now()
        transition = self._check_transition(
            TicketStatus.SPEC_APPROVAL, actor=actor, comment=None, now=ts
        )
        self.department_id = department_id
        self._apply_transition(TicketStatus.SPEC_APPROVAL, now=ts)
        return transition

    def return_for_refinement(
        self,
        *,
        actor: str,
        comment: str,
        now: datetime | None = None,
    ) -> StatusTransition:
        """Edge 3 [PO]: triage → created.

        ``comment`` is mandatory on this edge.
        """
        if not comment or not comment.strip():
            raise TicketValidationError(
                "Comment is required when returning a ticket for refinement"
            )
        ts = now or _now()
        transition = self._check_transition(
            TicketStatus.CREATED, actor=actor, comment=comment, now=ts
        )
        self._apply_transition(TicketStatus.CREATED, now=ts)
        return transition

    def reject(
        self,
        *,
        actor: str,
        comment: str,
        now: datetime | None = None,
    ) -> StatusTransition:
        """Edges 4 (triage → rejected) and 10 (acceptance → rejected) [PO].

        ``comment`` is mandatory on this edge.
        """
        if not comment or not comment.strip():
            raise TicketValidationError(
                "Comment is required when rejecting a ticket"
            )
        ts = now or _now()
        transition = self._check_transition(
            TicketStatus.REJECTED, actor=actor, comment=comment, now=ts
        )
        self._apply_transition(TicketStatus.REJECTED, now=ts)
        return transition

    def start_work(
        self,
        *,
        actor: str,
        now: datetime | None = None,
    ) -> StatusTransition:
        """Edge 5: spec_approval → in_progress.

        Guards (SpecApprovalGate, executor assignment) are called by TicketService.
        TrackerPort.mirror_create is called best-effort by TicketService after this.
        """
        ts = now or _now()
        transition = self._check_transition(
            TicketStatus.IN_PROGRESS, actor=actor, comment=None, now=ts
        )
        self._apply_transition(TicketStatus.IN_PROGRESS, now=ts)
        return transition

    def finish_work(
        self,
        *,
        actor: str,
        now: datetime | None = None,
    ) -> StatusTransition:
        """Edge 6: in_progress → change_capture.

        This is the only edge in SYSTEM_ACTOR_ALLOWED_EDGES (webhook path in MVP).
        """
        ts = now or _now()
        transition = self._check_transition(
            TicketStatus.CHANGE_CAPTURE, actor=actor, comment=None, now=ts
        )
        self._apply_transition(TicketStatus.CHANGE_CAPTURE, now=ts)
        return transition

    def request_acceptance(
        self,
        *,
        actor: str,
        changes_summary: str,
        now: datetime | None = None,
    ) -> StatusTransition:
        """Edge 7: change_capture → acceptance.

        ``changes_summary`` (was/is description) is mandatory; stored as
        StatusTransition.comment (Phase-1 substitute for ChangeRecord).
        """
        if not changes_summary or not changes_summary.strip():
            raise TicketValidationError(
                "changes_summary is required when requesting acceptance"
            )
        ts = now or _now()
        transition = self._check_transition(
            TicketStatus.ACCEPTANCE, actor=actor, comment=changes_summary, now=ts
        )
        self._apply_transition(TicketStatus.ACCEPTANCE, now=ts)
        return transition

    def close(
        self,
        *,
        actor: str,
        now: datetime | None = None,
    ) -> StatusTransition:
        """Edge 8: acceptance → closed.

        Guard (AcceptanceGate) is called by TicketService before this method.
        """
        ts = now or _now()
        transition = self._check_transition(
            TicketStatus.CLOSED, actor=actor, comment=None, now=ts
        )
        self._apply_transition(TicketStatus.CLOSED, now=ts)
        return transition

    def return_to_work(
        self,
        *,
        actor: str,
        comment: str,
        now: datetime | None = None,
    ) -> StatusTransition:
        """Edge 9 [PO]: acceptance → in_progress.

        Increments ``acceptance_cycle`` so that old acceptance attestations are
        invalidated for the gate; spec_approved (cycle 0) survives.
        ``comment`` is mandatory.
        """
        if not comment or not comment.strip():
            raise TicketValidationError(
                "Comment is required when returning a ticket to work"
            )
        ts = now or _now()
        old_cycle = self.acceptance_cycle
        transition = self._check_transition(
            TicketStatus.IN_PROGRESS, actor=actor, comment=comment, now=ts
        )
        # Increment cycle BEFORE building the transition record so the
        # transition row records the NEW cycle (post-increment).
        self.acceptance_cycle += 1
        # Rebuild transition with updated acceptance_cycle
        transition = StatusTransition(
            id=transition.id,
            ticket_id=self.id,
            from_status=TicketStatus.ACCEPTANCE,
            to_status=TicketStatus.IN_PROGRESS,
            actor=actor,
            comment=comment,
            acceptance_cycle=self.acceptance_cycle,
            occurred_at=ts,
        )
        self._apply_transition(TicketStatus.IN_PROGRESS, now=ts)
        # Store old_cycle on transition for event payload convenience
        transition = StatusTransition(
            id=transition.id,
            ticket_id=self.id,
            from_status=TicketStatus.ACCEPTANCE,
            to_status=TicketStatus.IN_PROGRESS,
            actor=actor,
            comment=comment,
            acceptance_cycle=self.acceptance_cycle,
            occurred_at=ts,
        )
        _ = old_cycle  # referenced by service for event payload
        return transition

    # ------------------------------------------------------------------ #
    # Attestation cycle queries (pure, no IO)                             #
    # ------------------------------------------------------------------ #

    def current_acceptance_cycle(self) -> int:
        return self.acceptance_cycle
