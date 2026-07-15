"""Service-level tests for TicketService using an in-memory fake repository.

The attestation gates under test are the REAL Phase-1 adapters
(AttestationSpecApprovalGate / AttestationAcceptanceGate) running against the
fake repository — so the dual-acceptance wiring is exercised end to end:

- Full happy-path lifecycle: create → submit → triage-accept → attest spec →
  start work → finish → request acceptance → both attestations → close.
- Gate failures: start-work without spec attestation; close without both
  acceptance attestations.
- Per-object rights: business_value only by the active business_owner.
- return_to_work increments the cycle and invalidates old attestations.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from app.modules.tickets.application.dtos import (
    AttestBusinessValueCommand,
    AttestFormalDodCommand,
    AttestSpecApprovalCommand,
    CloseCommand,
    CreateTicketCommand,
    FinishWorkCommand,
    RequestAcceptanceCommand,
    ReturnToWorkCommand,
    StartWorkCommand,
    SubmitCommand,
    TriageAcceptCommand,
)
from app.modules.tickets.application.services import TicketService
from app.modules.tickets.domain.entities import (
    Assignment,
    AssignmentRole,
    GateAttestation,
    IntakeSubmission,
    OpenDefectsResult,
    StatusTransition,
    Ticket,
    TicketStatus,
    TriageDecision,
)
from app.modules.tickets.domain.errors import (
    AcceptanceGateIncompleteError,
    AttestationNotAllowedError,
    SpecNotApprovedError,
    TicketAccessDeniedError,
)
from app.modules.tickets.infrastructure.gates import (
    AttestationAcceptanceGate,
    AttestationSpecApprovalGate,
)

_TS = datetime(2026, 7, 6, 12, 0, tzinfo=UTC)
_AUTHOR_ID = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
_AUTHOR_SUB = str(_AUTHOR_ID)
_BA_SUB = "ba-subject"
_BO_SUB = "business-owner-subject"
_EXEC_SUB = "executor-subject"
_DEPT_ID = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
_TPL_VERSION_ID = UUID("20000000-0000-0000-0000-000000000001")


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeRepo:
    def __init__(self) -> None:
        self.tickets: dict[UUID, Ticket] = {}
        self.transitions: list[StatusTransition] = []
        self.submissions: list[IntakeSubmission] = []
        self.triage_decisions: list[TriageDecision] = []
        self.assignments: dict[UUID, Assignment] = {}
        self.attestations: list[GateAttestation] = []

    async def add_ticket(self, ticket: Ticket) -> None:
        self.tickets[ticket.id] = dataclasses.replace(ticket)

    async def get_ticket_by_id(self, ticket_id: UUID) -> Ticket | None:
        t = self.tickets.get(ticket_id)
        return dataclasses.replace(t) if t is not None else None

    async def update_ticket(self, ticket: Ticket) -> None:
        self.tickets[ticket.id] = dataclasses.replace(ticket)

    async def list_tickets(
        self,
        *,
        status: TicketStatus | None,
        department_id: UUID | None,
        author_id: UUID | None,
        assigned_to: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Ticket], int]:
        rows = list(self.tickets.values())
        if status is not None:
            rows = [t for t in rows if t.status == status]
        if author_id is not None:
            rows = [t for t in rows if t.author_id == author_id]
        return rows[offset : offset + limit], len(rows)

    async def add_transition(self, transition: StatusTransition) -> None:
        self.transitions.append(transition)

    async def list_transitions(self, ticket_id: UUID) -> list[StatusTransition]:
        return [t for t in self.transitions if t.ticket_id == ticket_id]

    async def add_submission(self, submission: IntakeSubmission) -> None:
        self.submissions.append(submission)

    async def get_current_submission(self, ticket_id: UUID) -> IntakeSubmission | None:
        rows = [s for s in self.submissions if s.ticket_id == ticket_id]
        return max(rows, key=lambda s: s.version) if rows else None

    async def list_submissions(self, ticket_id: UUID) -> list[IntakeSubmission]:
        return [s for s in self.submissions if s.ticket_id == ticket_id]

    async def add_triage_decision(self, decision: TriageDecision) -> None:
        self.triage_decisions.append(decision)

    async def list_triage_decisions(self, ticket_id: UUID) -> list[TriageDecision]:
        return [d for d in self.triage_decisions if d.ticket_id == ticket_id]

    async def add_assignment(self, assignment: Assignment) -> None:
        self.assignments[assignment.id] = assignment

    async def get_active_assignment(
        self, ticket_id: UUID, role: AssignmentRole
    ) -> Assignment | None:
        for a in self.assignments.values():
            if a.ticket_id == ticket_id and a.role == role and a.unassigned_at is None:
                return a
        return None

    async def end_assignment(self, assignment_id: UUID, *, now: datetime) -> None:
        self.assignments[assignment_id].unassigned_at = now

    async def add_attestation(self, attestation: GateAttestation) -> None:
        self.attestations.append(attestation)

    async def get_attestation(
        self, ticket_id: UUID, kind: str, cycle: int
    ) -> GateAttestation | None:
        for a in self.attestations:
            if a.ticket_id == ticket_id and a.kind == kind and a.acceptance_cycle == cycle:
                return a
        return None

    async def list_attestations(self, ticket_id: UUID) -> list[GateAttestation]:
        return [a for a in self.attestations if a.ticket_id == ticket_id]


class FakeValidator:
    """SubmissionValidator returning no errors."""

    async def validate_submission(
        self, *, template_version_id: UUID, payload: dict[str, object]
    ) -> list[Any]:
        return []


class FakeDeptLookup:
    async def is_active_department(self, department_id: UUID) -> bool:
        return department_id == _DEPT_ID


class FakeVersionInfo:
    async def is_published_version(self, version_id: UUID) -> bool:
        return version_id == _TPL_VERSION_ID


class FakeTracker:
    async def mirror_create(self, ticket: Ticket) -> str | None:
        return None

    async def mirror_transition(self, **kwargs: object) -> None:
        return None

    async def pull_updates(self, *, since: datetime) -> list[Any]:
        return []

    async def open_defects_query(
        self, *, ticket_id: UUID, external_ref: str | None
    ) -> OpenDefectsResult:
        return OpenDefectsResult(open_p0=None, open_p1=None, checked_at=_TS)


class RecordingPublisher:
    def __init__(self) -> None:
        self.events: list[tuple[str, UUID, str]] = []

    async def __call__(
        self,
        entity_type: str,
        entity_id: UUID,
        action: str,
        *,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> None:
        self.events.append((entity_type, entity_id, action))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def repo() -> FakeRepo:
    return FakeRepo()


@pytest.fixture
def publisher() -> RecordingPublisher:
    return RecordingPublisher()


@pytest.fixture
def service(repo: FakeRepo, publisher: RecordingPublisher) -> TicketService:
    return TicketService(
        repo=repo,
        validator=FakeValidator(),
        dept_lookup=FakeDeptLookup(),
        version_info=FakeVersionInfo(),
        spec_gate=AttestationSpecApprovalGate(repo),
        acceptance_gate=AttestationAcceptanceGate(repo),
        tracker=FakeTracker(),
        publisher=publisher,
        clock=lambda: _TS,
    )


async def _create_ticket(service: TicketService) -> Ticket:
    ticket, _, _ = await service.create_ticket(
        actor_id=_AUTHOR_ID,
        actor_sub=_AUTHOR_SUB,
        roles=frozenset({"tenant_user"}),
        command=CreateTicketCommand(
            title="Автоподстановка тарифа",
            template_version_id=_TPL_VERSION_ID,
            payload={"problem": "ручной ввод"},
        ),
    )
    return ticket


async def _advance_to_acceptance(service: TicketService, ticket_id: UUID) -> None:
    """Drive a fresh ticket to status=acceptance along the happy path."""
    await service.submit(
        ticket_id=ticket_id,
        actor_id=_AUTHOR_ID,
        actor_sub=_AUTHOR_SUB,
        roles=frozenset({"tenant_user"}),
        command=SubmitCommand(),
    )
    await service.triage_accept(
        ticket_id=ticket_id,
        actor_sub=_BA_SUB,
        roles=frozenset({"ba"}),
        command=TriageAcceptCommand(
            department_id=_DEPT_ID,
            business_owner_subject=_BO_SUB,
            executor_subject=_EXEC_SUB,
        ),
    )
    await service.attest_spec_approval(
        ticket_id=ticket_id,
        actor_sub=_BA_SUB,
        roles=frozenset({"ba"}),
        command=AttestSpecApprovalCommand(
            spec_ref="confluence://spec-1", agreed_with_subject=_BO_SUB
        ),
    )
    await service.start_work(
        ticket_id=ticket_id,
        actor_id=_AUTHOR_ID,
        actor_sub=_EXEC_SUB,
        roles=frozenset(),
        command=StartWorkCommand(),
    )
    await service.finish_work(
        ticket_id=ticket_id,
        actor_id=_AUTHOR_ID,
        actor_sub=_EXEC_SUB,
        roles=frozenset(),
        command=FinishWorkCommand(),
    )
    await service.request_acceptance(
        ticket_id=ticket_id,
        actor_id=_AUTHOR_ID,
        actor_sub=_EXEC_SUB,
        roles=frozenset(),
        command=RequestAcceptanceCommand(changes_summary="время подключения снижено вдвое"),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_full_lifecycle_closes_through_dual_gate(
    service: TicketService, repo: FakeRepo, publisher: RecordingPublisher
) -> None:
    ticket = await _create_ticket(service)
    await _advance_to_acceptance(service, ticket.id)

    await service.attest_formal_dod(
        ticket_id=ticket.id,
        actor_sub=_BA_SUB,
        roles=frozenset({"ba"}),
        command=AttestFormalDodCommand(checklist={"acs_met": True, "before_after_recorded": True}),
    )
    await service.attest_business_value(
        ticket_id=ticket.id,
        actor_sub=_BO_SUB,
        roles=frozenset(),
        command=AttestBusinessValueCommand(comment="минус 30% времени подключения"),
    )
    closed, _ = await service.close(
        ticket_id=ticket.id,
        actor_sub=_BA_SUB,
        roles=frozenset({"ba"}),
        command=CloseCommand(),
    )

    assert closed.status == TicketStatus.CLOSED
    assert closed.closed_at == _TS
    actions = [a for _, _, a in publisher.events]
    assert actions == [
        "created",
        "submitted",
        "triage_accepted",
        "spec_approval_attested",
        "work_started",
        "work_finished",
        "acceptance_requested",
        "formal_dod_attested",
        "business_value_attested",
        "closed",
    ]
    # One transition row per status change + creation row
    assert len(repo.transitions) == 7


async def test_start_work_blocked_without_spec_attestation(
    service: TicketService,
) -> None:
    ticket = await _create_ticket(service)
    await service.submit(
        ticket_id=ticket.id,
        actor_id=_AUTHOR_ID,
        actor_sub=_AUTHOR_SUB,
        roles=frozenset({"tenant_user"}),
        command=SubmitCommand(),
    )
    await service.triage_accept(
        ticket_id=ticket.id,
        actor_sub=_BA_SUB,
        roles=frozenset({"ba"}),
        command=TriageAcceptCommand(
            department_id=_DEPT_ID,
            business_owner_subject=_BO_SUB,
            executor_subject=_EXEC_SUB,
        ),
    )

    with pytest.raises(SpecNotApprovedError):
        await service.start_work(
            ticket_id=ticket.id,
            actor_id=_AUTHOR_ID,
            actor_sub=_BA_SUB,
            roles=frozenset({"ba"}),
            command=StartWorkCommand(),
        )


async def test_close_blocked_until_both_attestations(service: TicketService) -> None:
    ticket = await _create_ticket(service)
    await _advance_to_acceptance(service, ticket.id)

    # No attestations at all
    with pytest.raises(AcceptanceGateIncompleteError):
        await service.close(
            ticket_id=ticket.id,
            actor_sub=_BA_SUB,
            roles=frozenset({"ba"}),
            command=CloseCommand(),
        )

    # Only the formal gate — business value still missing
    await service.attest_formal_dod(
        ticket_id=ticket.id,
        actor_sub=_BA_SUB,
        roles=frozenset({"ba"}),
        command=AttestFormalDodCommand(checklist={"acs_met": True}),
    )
    with pytest.raises(AcceptanceGateIncompleteError):
        await service.close(
            ticket_id=ticket.id,
            actor_sub=_BA_SUB,
            roles=frozenset({"ba"}),
            command=CloseCommand(),
        )


async def test_business_value_only_by_active_business_owner(
    service: TicketService,
) -> None:
    ticket = await _create_ticket(service)
    await _advance_to_acceptance(service, ticket.id)

    # BA cannot sign the value gate — even with the ba role
    with pytest.raises(AttestationNotAllowedError):
        await service.attest_business_value(
            ticket_id=ticket.id,
            actor_sub=_BA_SUB,
            roles=frozenset({"ba", "tenant_admin"}),
            command=AttestBusinessValueCommand(),
        )


async def test_return_to_work_invalidates_old_cycle_attestations(
    service: TicketService,
) -> None:
    ticket = await _create_ticket(service)
    await _advance_to_acceptance(service, ticket.id)

    await service.attest_formal_dod(
        ticket_id=ticket.id,
        actor_sub=_BA_SUB,
        roles=frozenset({"ba"}),
        command=AttestFormalDodCommand(checklist={"acs_met": True}),
    )
    await service.attest_business_value(
        ticket_id=ticket.id,
        actor_sub=_BO_SUB,
        roles=frozenset(),
        command=AttestBusinessValueCommand(),
    )

    # Business owner returns the ticket to work → cycle 1 → 2
    returned, _ = await service.return_to_work(
        ticket_id=ticket.id,
        actor_sub=_BO_SUB,
        roles=frozenset(),
        command=ReturnToWorkCommand(comment="метрика не достигнута"),
    )
    assert returned.status == TicketStatus.IN_PROGRESS
    assert returned.acceptance_cycle == 2

    # Drive back to acceptance; cycle-1 attestations must NOT satisfy the gate
    await service.finish_work(
        ticket_id=ticket.id,
        actor_id=_AUTHOR_ID,
        actor_sub=_EXEC_SUB,
        roles=frozenset(),
        command=FinishWorkCommand(),
    )
    await service.request_acceptance(
        ticket_id=ticket.id,
        actor_id=_AUTHOR_ID,
        actor_sub=_EXEC_SUB,
        roles=frozenset(),
        command=RequestAcceptanceCommand(changes_summary="повторная сдача"),
    )
    with pytest.raises(AcceptanceGateIncompleteError):
        await service.close(
            ticket_id=ticket.id,
            actor_sub=_BA_SUB,
            roles=frozenset({"ba"}),
            command=CloseCommand(),
        )


async def test_triage_requires_ba_role(service: TicketService) -> None:
    ticket = await _create_ticket(service)
    await service.submit(
        ticket_id=ticket.id,
        actor_id=_AUTHOR_ID,
        actor_sub=_AUTHOR_SUB,
        roles=frozenset({"tenant_user"}),
        command=SubmitCommand(),
    )
    with pytest.raises(TicketAccessDeniedError):
        await service.triage_accept(
            ticket_id=ticket.id,
            actor_sub=_AUTHOR_SUB,
            roles=frozenset({"tenant_user"}),
            command=TriageAcceptCommand(department_id=_DEPT_ID, business_owner_subject=_BO_SUB),
        )


async def _advance_to_spec_approval(service: TicketService, ticket_id: UUID) -> None:
    """Drive a fresh ticket to status=spec_approval (ready to sign the spec)."""
    await service.submit(
        ticket_id=ticket_id,
        actor_id=_AUTHOR_ID,
        actor_sub=_AUTHOR_SUB,
        roles=frozenset({"tenant_user"}),
        command=SubmitCommand(),
    )
    await service.triage_accept(
        ticket_id=ticket_id,
        actor_sub=_BA_SUB,
        roles=frozenset({"ba"}),
        command=TriageAcceptCommand(
            department_id=_DEPT_ID,
            business_owner_subject=_BO_SUB,
            executor_subject=_EXEC_SUB,
        ),
    )


async def test_spec_approval_defaults_spec_ref_to_ticket_self_reference(
    service: TicketService,
) -> None:
    """The ticket IS the spec: when spec_ref is omitted the service pins a
    self-reference to the ticket and its current intake-submission version."""
    ticket = await _create_ticket(service)
    await _advance_to_spec_approval(service, ticket.id)

    attestation = await service.attest_spec_approval(
        ticket_id=ticket.id,
        actor_sub=_BA_SUB,
        roles=frozenset({"ba"}),
        command=AttestSpecApprovalCommand(agreed_with_subject=_BO_SUB),  # no spec_ref
    )

    assert attestation.spec_ref == f"ticket:{ticket.id}#submission-v1"


async def test_spec_approval_honours_explicit_spec_ref(service: TicketService) -> None:
    """An explicit external reference (Confluence/tracker URL) is still honoured."""
    ticket = await _create_ticket(service)
    await _advance_to_spec_approval(service, ticket.id)

    attestation = await service.attest_spec_approval(
        ticket_id=ticket.id,
        actor_sub=_BA_SUB,
        roles=frozenset({"ba"}),
        command=AttestSpecApprovalCommand(
            spec_ref="confluence://external-spec", agreed_with_subject=_BO_SUB
        ),
    )

    assert attestation.spec_ref == "confluence://external-spec"
