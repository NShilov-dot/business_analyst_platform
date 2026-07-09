"""Port contracts for the tickets module.

All ports are typing.Protocol — framework-free.

SubmissionValidator is intentionally NOT re-declared here; it is imported from
app.modules.intake_templates.domain.ports where it lives as the canonical
contract.  TicketService injects a TemplateService instance that satisfies it
structurally.

Declared ports:
- TicketRepository  — persistence (implemented in infrastructure)
- TrackerPort       — external tracker mirror (NullTrackerGateway in Phase 1)
- SpecApprovalGate  — guard: in_progress only after spec approval
- AcceptanceGate    — guard: closed only after dual acceptance attestation
- DepartmentLookup  — thin seam: is a department active?
- TemplateVersionInfo — thin seam: is a template version published?
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.modules.tickets.domain.entities import (
    Assignment,
    AssignmentRole,
    GateAttestation,
    GateCheckResult,
    IntakeSubmission,
    OpenDefectsResult,
    StatusTransition,
    Ticket,
    TicketStatus,
    TriageDecision,
)

# ---------------------------------------------------------------------------
# TicketRepository
# ---------------------------------------------------------------------------


class TicketRepository(Protocol):
    """Persistence port for all ticket aggregate objects.

    Repositories call flush(), NEVER commit(). The SessionDep owns the
    transaction boundary.
    """

    # -- Ticket CRUD -------------------------------------------------------

    async def add_ticket(self, ticket: Ticket) -> None: ...

    async def get_ticket_by_id(self, ticket_id: UUID) -> Ticket | None: ...

    async def update_ticket(self, ticket: Ticket) -> None: ...

    async def list_tickets(
        self,
        *,
        status: TicketStatus | None,
        department_id: UUID | None,
        author_id: UUID | None,
        assigned_to: str | None,  # Keycloak sub — filter by active assignment
        limit: int,
        offset: int,
    ) -> tuple[list[Ticket], int]: ...

    # -- StatusTransition --------------------------------------------------

    async def add_transition(self, transition: StatusTransition) -> None: ...

    async def list_transitions(self, ticket_id: UUID) -> list[StatusTransition]: ...

    # -- IntakeSubmission --------------------------------------------------

    async def add_submission(self, submission: IntakeSubmission) -> None: ...

    async def get_current_submission(self, ticket_id: UUID) -> IntakeSubmission | None:
        """Return the highest-version submission for the ticket."""
        ...

    async def list_submissions(self, ticket_id: UUID) -> list[IntakeSubmission]: ...

    # -- TriageDecision ----------------------------------------------------

    async def add_triage_decision(self, decision: TriageDecision) -> None: ...

    async def list_triage_decisions(self, ticket_id: UUID) -> list[TriageDecision]: ...

    # -- Assignment --------------------------------------------------------

    async def add_assignment(self, assignment: Assignment) -> None: ...

    async def get_active_assignment(
        self, ticket_id: UUID, role: AssignmentRole
    ) -> Assignment | None: ...

    async def end_assignment(self, assignment_id: UUID, *, now: datetime) -> None:
        """Set unassigned_at on the given assignment row."""
        ...

    # -- GateAttestation ---------------------------------------------------

    async def add_attestation(self, attestation: GateAttestation) -> None: ...

    async def get_attestation(
        self,
        ticket_id: UUID,
        kind: str,
        cycle: int,
    ) -> GateAttestation | None: ...

    async def list_attestations(self, ticket_id: UUID) -> list[GateAttestation]: ...

    # -- Metrics -----------------------------------------------------------

    async def intake_share(
        self,
        *,
        created_from: datetime | None,
        created_to: datetime | None,
    ) -> list[tuple[UUID, int]]:
        """Return [(template_version_id, count)] by current submission.

        Each ticket is counted once — by its highest-version submission's
        template_version_id.  Used by the intake-share stats endpoint.
        """
        ...


# ---------------------------------------------------------------------------
# TrackerPort
# ---------------------------------------------------------------------------


class TrackerUpdate(Protocol):
    """A status update received from the external tracker."""

    ticket_id: UUID
    external_ref: str
    new_status: TicketStatus
    occurred_at: datetime


class TrackerPort(Protocol):
    """Port for mirroring ticket state to/from an external issue tracker.

    All operations are best-effort: errors must NOT block the local transaction.
    The caller (TicketService) catches all exceptions and logs them; Postgres
    remains the system of record.

    NullTrackerGateway is the Phase-1 adapter (no-op / [] / UNKNOWN).
    """

    async def mirror_create(self, ticket: Ticket) -> str | None:
        """Create an issue in the tracker and return its external reference.

        Returns None if the tracker is unavailable or the operation failed.
        """
        ...

    async def mirror_transition(
        self,
        *,
        ticket_id: UUID,
        external_ref: str,
        from_status: TicketStatus,
        to_status: TicketStatus,
    ) -> None:
        """Notify the tracker of a status change."""
        ...

    async def pull_updates(self, *, since: datetime) -> list[TrackerUpdate]:
        """Poll for updates from the tracker since the given timestamp."""
        ...

    async def open_defects_query(
        self,
        *,
        ticket_id: UUID,
        external_ref: str | None,
    ) -> OpenDefectsResult:
        """Query the tracker for open P0/P1 defects linked to this ticket.

        IMPORTANT: Returns OpenDefectsResult with open_p0=None, open_p1=None
        when the tracker is unavailable or the result is UNKNOWN.
        NEVER returns 0 as a "clean" sentinel — only an explicit 0 means
        "confirmed zero open defects".
        """
        ...


# ---------------------------------------------------------------------------
# SpecApprovalGate
# ---------------------------------------------------------------------------


class SpecApprovalGate(Protocol):
    """Guard: spec_approval → in_progress is only allowed when this gate is satisfied.

    Phase-1 adapter: AttestationSpecApprovalGate — ok iff spec_approved
    attestation (cycle 0) exists for the ticket.
    MVP adapter: RequirementsSpecApprovalGate — ok iff ArtifactStatus == Approved.
    """

    async def is_satisfied(self, ticket_id: UUID) -> GateCheckResult: ...


# ---------------------------------------------------------------------------
# AcceptanceGate
# ---------------------------------------------------------------------------


class AcceptanceGate(Protocol):
    """Guard: acceptance → closed only when BOTH attestations for current cycle exist.

    Phase-1 adapter: AttestationAcceptanceGate — ok iff both formal_dod and
    business_value attestations exist for the ticket's current acceptance_cycle.

    WHO signs is enforced by TicketService, not this gate.
    """

    async def is_satisfied(
        self, ticket_id: UUID, *, acceptance_cycle: int
    ) -> GateCheckResult: ...


# ---------------------------------------------------------------------------
# DepartmentLookup — thin seam
# ---------------------------------------------------------------------------


class DepartmentLookup(Protocol):
    """Thin seam: check whether a department exists and is active.

    Backed by SqlAlchemyDepartmentRepository in the composition root.
    Prevents routing to deactivated departments at triage.
    """

    async def is_active_department(self, department_id: UUID) -> bool: ...


# ---------------------------------------------------------------------------
# TemplateVersionInfo — thin seam
# ---------------------------------------------------------------------------


class TemplateVersionInfo(Protocol):
    """Thin seam: check whether a template version is published.

    Closes the confirmed gap: TemplateService.validate_submission does NOT
    check the version's publication status, so tickets must verify it
    before accepting a new submission version.
    """

    async def is_published_version(self, version_id: UUID) -> bool: ...
