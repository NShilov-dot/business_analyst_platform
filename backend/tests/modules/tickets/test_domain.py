"""Domain-layer tests for the tickets module.

Covers:
- TicketStatus values and ALLOWED_TRANSITIONS completeness
- Ticket.create factory and validation
- Every allowed edge: succeeds, mutates status, returns StatusTransition
- Every disallowed (from, to) pair: raises TicketTransitionForbiddenError
- Comment-mandatory edges (return_for_refinement, reject, request_acceptance, return_to_work)
- update_details: allowed statuses, blocked in terminal
- assert_submission_editable: blocks outside created
- accept_triage sets department_id
- return_to_work increments acceptance_cycle; spec_approved (cycle 0) semantics
- SYSTEM_ACTOR_ALLOWED_EDGES membership
- GateCheckResult factory methods
- OpenDefectsResult: None semantics (unknown != zero)
- StatusTransition fields (from_status None on creation, correct acceptance_cycle)
- GateAttestation invariants (cycle=0 for spec_approved, >=1 for others, spec_ref required)
- TriageDecision invariants (accepted needs dept, rejected needs reason, duplicate needs id)
- Assignment.is_active property
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.modules.tickets.domain.entities import (
    ALLOWED_TRANSITIONS,
    SYSTEM_ACTOR_ALLOWED_EDGES,
    SYSTEM_TRACKER_ACTOR,
    Assignment,
    AssignmentRole,
    AttestationKind,
    GateAttestation,
    GateCheckResult,
    OpenDefectsResult,
    RejectionReason,
    Ticket,
    TicketPriority,
    TicketStatus,
    TriageDecision,
    TriageOutcome,
)
from app.modules.tickets.domain.errors import (
    AssignmentMissingError,
    TicketSubmissionFrozenError,
    TicketTransitionForbiddenError,
    TicketValidationError,
)

_NOW = datetime(2026, 7, 4, 10, 0, tzinfo=UTC)
_ACTOR = "actor-sub-uuid"
_DEPT = uuid4()


def _make_ticket(
    *,
    status: TicketStatus = TicketStatus.CREATED,
    author_id: UUID | None = None,
    department_id: UUID | None = None,
    acceptance_cycle: int = 1,
) -> Ticket:
    t = Ticket.create(author_id=author_id or uuid4(), title="Test ticket", now=_NOW)
    t.status = status
    t.department_id = department_id
    t.acceptance_cycle = acceptance_cycle
    return t


# ===========================================================================
# TicketStatus enum
# ===========================================================================


class TestTicketStatusEnum:
    def test_all_8_values_present(self) -> None:
        values = {s.value for s in TicketStatus}
        assert values == {
            "created",
            "triage",
            "spec_approval",
            "in_progress",
            "change_capture",
            "acceptance",
            "closed",
            "rejected",
        }

    def test_allowed_transitions_covers_all_statuses(self) -> None:
        # Every TicketStatus should be a key in ALLOWED_TRANSITIONS
        for status in TicketStatus:
            assert status in ALLOWED_TRANSITIONS, f"{status!s} missing from ALLOWED_TRANSITIONS"

    def test_terminal_statuses_have_empty_transitions(self) -> None:
        assert ALLOWED_TRANSITIONS[TicketStatus.CLOSED] == frozenset()
        assert ALLOWED_TRANSITIONS[TicketStatus.REJECTED] == frozenset()


# ===========================================================================
# SYSTEM_ACTOR_ALLOWED_EDGES
# ===========================================================================


class TestSystemActorAllowedEdges:
    def test_only_in_progress_to_change_capture(self) -> None:
        assert frozenset({("in_progress", "change_capture")}) == SYSTEM_ACTOR_ALLOWED_EDGES

    def test_system_tracker_actor_constant(self) -> None:
        assert SYSTEM_TRACKER_ACTOR == "system:tracker"


# ===========================================================================
# GateCheckResult
# ===========================================================================


class TestGateCheckResult:
    def test_passed_factory(self) -> None:
        r = GateCheckResult.passed()
        assert r.ok is True
        assert r.reasons == ()

    def test_failed_factory_with_reasons(self) -> None:
        r = GateCheckResult.failed("missing spec_ref", "no business_owner")
        assert r.ok is False
        assert "missing spec_ref" in r.reasons

    def test_frozen(self) -> None:
        r = GateCheckResult.passed()
        with pytest.raises((AttributeError, TypeError)):
            r.ok = False  # type: ignore[misc]


# ===========================================================================
# OpenDefectsResult
# ===========================================================================


class TestOpenDefectsResult:
    def test_none_means_unknown_not_zero(self) -> None:
        r = OpenDefectsResult(open_p0=None, open_p1=None, checked_at=_NOW)
        # None is unknown — the caller must treat None != 0
        assert r.open_p0 is None
        assert r.open_p1 is None

    def test_zero_means_confirmed_clean(self) -> None:
        r = OpenDefectsResult(open_p0=0, open_p1=0, checked_at=_NOW)
        assert r.open_p0 == 0
        assert r.open_p1 == 0


# ===========================================================================
# Ticket.create
# ===========================================================================


class TestTicketCreate:
    def test_factory_sets_created_status(self) -> None:
        t = Ticket.create(author_id=uuid4(), title="Ship it", now=_NOW)
        assert t.status is TicketStatus.CREATED

    def test_factory_acceptance_cycle_starts_at_1(self) -> None:
        t = Ticket.create(author_id=uuid4(), title="Ship it", now=_NOW)
        assert t.acceptance_cycle == 1

    def test_factory_department_id_is_none(self) -> None:
        t = Ticket.create(author_id=uuid4(), title="Ship it", now=_NOW)
        assert t.department_id is None

    def test_factory_closed_at_is_none(self) -> None:
        t = Ticket.create(author_id=uuid4(), title="Ship it", now=_NOW)
        assert t.closed_at is None

    def test_trims_title(self) -> None:
        t = Ticket.create(author_id=uuid4(), title="  hello  ", now=_NOW)
        assert t.title == "hello"

    def test_rejects_blank_title(self) -> None:
        with pytest.raises(TicketValidationError):
            Ticket.create(author_id=uuid4(), title="   ")

    def test_rejects_too_long_title(self) -> None:
        with pytest.raises(TicketValidationError):
            Ticket.create(author_id=uuid4(), title="x" * 201)

    def test_injected_now_is_deterministic(self) -> None:
        t = Ticket.create(author_id=uuid4(), title="T", now=_NOW)
        assert t.created_at == _NOW
        assert t.updated_at == _NOW


# ===========================================================================
# update_details
# ===========================================================================


class TestUpdateDetails:
    def test_allowed_in_created(self) -> None:
        t = _make_ticket(status=TicketStatus.CREATED)
        t.update_details(title="New title")
        assert t.title == "New title"

    def test_allowed_in_triage(self) -> None:
        t = _make_ticket(status=TicketStatus.TRIAGE)
        t.update_details(title="Updated")
        assert t.title == "Updated"

    def test_blocked_in_spec_approval(self) -> None:
        t = _make_ticket(status=TicketStatus.SPEC_APPROVAL)
        with pytest.raises(TicketTransitionForbiddenError):
            t.update_details(title="Too late")

    def test_blocked_in_closed(self) -> None:
        t = _make_ticket(status=TicketStatus.CLOSED)
        with pytest.raises(TicketTransitionForbiddenError):
            t.update_details(title="Too late")

    def test_nullable_description_requires_set_flag(self) -> None:
        t = _make_ticket()
        t.description = "original"
        t.update_details(description=None, set_description=False)
        assert t.description == "original"
        t.update_details(description=None, set_description=True)
        assert t.description is None

    def test_nullable_priority_requires_set_flag(self) -> None:
        t = _make_ticket()
        t.priority = TicketPriority.HIGH
        t.update_details(priority=None, set_priority=False)
        assert t.priority is TicketPriority.HIGH
        t.update_details(priority=None, set_priority=True)
        assert t.priority is None


# ===========================================================================
# assert_submission_editable
# ===========================================================================


class TestAssertSubmissionEditable:
    def test_passes_in_created(self) -> None:
        t = _make_ticket(status=TicketStatus.CREATED)
        t.assert_submission_editable()  # should not raise

    @pytest.mark.parametrize(
        "status",
        [
            TicketStatus.TRIAGE,
            TicketStatus.SPEC_APPROVAL,
            TicketStatus.IN_PROGRESS,
            TicketStatus.CHANGE_CAPTURE,
            TicketStatus.ACCEPTANCE,
            TicketStatus.CLOSED,
            TicketStatus.REJECTED,
        ],
    )
    def test_blocks_outside_created(self, status: TicketStatus) -> None:
        t = _make_ticket(status=status)
        with pytest.raises(TicketSubmissionFrozenError):
            t.assert_submission_editable()


# ===========================================================================
# Allowed transition edges — every edge must succeed
# ===========================================================================


class TestAllowedEdges:
    def test_edge_created_to_triage_submit(self) -> None:
        t = _make_ticket(status=TicketStatus.CREATED)
        tr = t.submit(actor=_ACTOR, now=_NOW)
        assert t.status is TicketStatus.TRIAGE
        assert tr.from_status is TicketStatus.CREATED
        assert tr.to_status is TicketStatus.TRIAGE
        assert tr.actor == _ACTOR
        assert tr.occurred_at == _NOW

    def test_edge_triage_to_spec_approval_accept(self) -> None:
        t = _make_ticket(status=TicketStatus.TRIAGE)
        tr = t.accept_triage(actor=_ACTOR, department_id=_DEPT, now=_NOW)
        assert t.status is TicketStatus.SPEC_APPROVAL
        assert t.department_id == _DEPT
        assert tr.from_status is TicketStatus.TRIAGE
        assert tr.to_status is TicketStatus.SPEC_APPROVAL

    def test_edge_triage_to_created_return_for_refinement(self) -> None:
        t = _make_ticket(status=TicketStatus.TRIAGE)
        tr = t.return_for_refinement(actor=_ACTOR, comment="needs more detail", now=_NOW)
        assert t.status is TicketStatus.CREATED
        assert tr.from_status is TicketStatus.TRIAGE
        assert tr.to_status is TicketStatus.CREATED
        assert tr.comment == "needs more detail"

    def test_edge_triage_to_rejected_reject(self) -> None:
        t = _make_ticket(status=TicketStatus.TRIAGE)
        tr = t.reject(actor=_ACTOR, comment="duplicate request", now=_NOW)
        assert t.status is TicketStatus.REJECTED
        assert t.closed_at == _NOW
        assert tr.from_status is TicketStatus.TRIAGE
        assert tr.to_status is TicketStatus.REJECTED

    def test_edge_spec_approval_to_in_progress_start_work(self) -> None:
        t = _make_ticket(status=TicketStatus.SPEC_APPROVAL)
        tr = t.start_work(actor=_ACTOR, now=_NOW)
        assert t.status is TicketStatus.IN_PROGRESS
        assert tr.to_status is TicketStatus.IN_PROGRESS

    def test_edge_in_progress_to_change_capture_finish_work(self) -> None:
        t = _make_ticket(status=TicketStatus.IN_PROGRESS)
        tr = t.finish_work(actor=_ACTOR, now=_NOW)
        assert t.status is TicketStatus.CHANGE_CAPTURE
        assert tr.to_status is TicketStatus.CHANGE_CAPTURE

    def test_edge_change_capture_to_acceptance_request_acceptance(self) -> None:
        t = _make_ticket(status=TicketStatus.CHANGE_CAPTURE)
        tr = t.request_acceptance(actor=_ACTOR, changes_summary="Was X, now Y", now=_NOW)
        assert t.status is TicketStatus.ACCEPTANCE
        assert tr.comment == "Was X, now Y"

    def test_edge_acceptance_to_closed_close(self) -> None:
        t = _make_ticket(status=TicketStatus.ACCEPTANCE)
        tr = t.close(actor=_ACTOR, now=_NOW)
        assert t.status is TicketStatus.CLOSED
        assert t.closed_at == _NOW
        assert tr.to_status is TicketStatus.CLOSED

    def test_edge_acceptance_to_in_progress_return_to_work(self) -> None:
        t = _make_ticket(status=TicketStatus.ACCEPTANCE, acceptance_cycle=1)
        tr = t.return_to_work(actor=_ACTOR, comment="gate failed", now=_NOW)
        assert t.status is TicketStatus.IN_PROGRESS
        assert t.acceptance_cycle == 2  # incremented
        assert tr.acceptance_cycle == 2  # transition records new cycle

    def test_edge_acceptance_to_rejected_reject(self) -> None:
        t = _make_ticket(status=TicketStatus.ACCEPTANCE)
        t.reject(actor=_ACTOR, comment="failing value gate", now=_NOW)
        assert t.status is TicketStatus.REJECTED
        assert t.closed_at == _NOW


# ===========================================================================
# Disallowed (from, to) pairs — every pair must raise TICKET_TRANSITION_FORBIDDEN
# ===========================================================================


class TestDisallowedEdges:
    @pytest.mark.parametrize(
        ("from_status", "to_status"),
        [
            # Terminal statuses cannot transition anywhere
            (TicketStatus.CLOSED, TicketStatus.CREATED),
            (TicketStatus.CLOSED, TicketStatus.TRIAGE),
            (TicketStatus.CLOSED, TicketStatus.IN_PROGRESS),
            (TicketStatus.REJECTED, TicketStatus.CREATED),
            (TicketStatus.REJECTED, TicketStatus.TRIAGE),
            # created can only go to triage
            (TicketStatus.CREATED, TicketStatus.SPEC_APPROVAL),
            (TicketStatus.CREATED, TicketStatus.IN_PROGRESS),
            (TicketStatus.CREATED, TicketStatus.CLOSED),
            (TicketStatus.CREATED, TicketStatus.REJECTED),
            # triage cannot skip to in_progress or later
            (TicketStatus.TRIAGE, TicketStatus.IN_PROGRESS),
            (TicketStatus.TRIAGE, TicketStatus.ACCEPTANCE),
            (TicketStatus.TRIAGE, TicketStatus.CLOSED),
            # spec_approval cannot go backwards
            (TicketStatus.SPEC_APPROVAL, TicketStatus.CREATED),
            (TicketStatus.SPEC_APPROVAL, TicketStatus.TRIAGE),
            (TicketStatus.SPEC_APPROVAL, TicketStatus.ACCEPTANCE),
            (TicketStatus.SPEC_APPROVAL, TicketStatus.CLOSED),
            # in_progress cannot skip ahead
            (TicketStatus.IN_PROGRESS, TicketStatus.ACCEPTANCE),
            (TicketStatus.IN_PROGRESS, TicketStatus.CLOSED),
            (TicketStatus.IN_PROGRESS, TicketStatus.CREATED),
            # change_capture cannot go backwards
            (TicketStatus.CHANGE_CAPTURE, TicketStatus.IN_PROGRESS),
            (TicketStatus.CHANGE_CAPTURE, TicketStatus.CLOSED),
            (TicketStatus.CHANGE_CAPTURE, TicketStatus.REJECTED),
            # acceptance cannot go to triage or spec_approval
            (TicketStatus.ACCEPTANCE, TicketStatus.TRIAGE),
            (TicketStatus.ACCEPTANCE, TicketStatus.SPEC_APPROVAL),
            (TicketStatus.ACCEPTANCE, TicketStatus.CREATED),
        ],
    )
    def test_disallowed_edge_raises(
        self, from_status: TicketStatus, to_status: TicketStatus
    ) -> None:
        t = _make_ticket(status=from_status)
        # We use the internal _check_transition to test raw edge validation
        with pytest.raises(TicketTransitionForbiddenError):
            t._check_transition(to_status, actor=_ACTOR, comment=None, now=_NOW)


# ===========================================================================
# Comment-mandatory edges
# ===========================================================================


class TestCommentMandatoryEdges:
    def test_return_for_refinement_requires_comment(self) -> None:
        t = _make_ticket(status=TicketStatus.TRIAGE)
        with pytest.raises(TicketValidationError):
            t.return_for_refinement(actor=_ACTOR, comment="")

    def test_return_for_refinement_requires_non_blank_comment(self) -> None:
        t = _make_ticket(status=TicketStatus.TRIAGE)
        with pytest.raises(TicketValidationError):
            t.return_for_refinement(actor=_ACTOR, comment="   ")

    def test_reject_requires_comment(self) -> None:
        t = _make_ticket(status=TicketStatus.TRIAGE)
        with pytest.raises(TicketValidationError):
            t.reject(actor=_ACTOR, comment="")

    def test_request_acceptance_requires_changes_summary(self) -> None:
        t = _make_ticket(status=TicketStatus.CHANGE_CAPTURE)
        with pytest.raises(TicketValidationError):
            t.request_acceptance(actor=_ACTOR, changes_summary="")

    def test_request_acceptance_requires_non_blank_summary(self) -> None:
        t = _make_ticket(status=TicketStatus.CHANGE_CAPTURE)
        with pytest.raises(TicketValidationError):
            t.request_acceptance(actor=_ACTOR, changes_summary="  ")

    def test_return_to_work_requires_comment(self) -> None:
        t = _make_ticket(status=TicketStatus.ACCEPTANCE)
        with pytest.raises(TicketValidationError):
            t.return_to_work(actor=_ACTOR, comment="")


# ===========================================================================
# acceptance_cycle increment on return_to_work
# ===========================================================================


class TestAcceptanceCycleIncrement:
    def test_first_return_increments_to_2(self) -> None:
        t = _make_ticket(status=TicketStatus.ACCEPTANCE, acceptance_cycle=1)
        t.return_to_work(actor=_ACTOR, comment="gate failed", now=_NOW)
        assert t.acceptance_cycle == 2

    def test_second_return_increments_to_3(self) -> None:
        t = _make_ticket(status=TicketStatus.ACCEPTANCE, acceptance_cycle=2)
        t.return_to_work(actor=_ACTOR, comment="again", now=_NOW)
        assert t.acceptance_cycle == 3

    def test_transition_records_new_cycle(self) -> None:
        t = _make_ticket(status=TicketStatus.ACCEPTANCE, acceptance_cycle=1)
        tr = t.return_to_work(actor=_ACTOR, comment="fail", now=_NOW)
        assert tr.acceptance_cycle == 2


# ===========================================================================
# accept_triage sets department_id
# ===========================================================================


class TestAcceptTriageSetsDepart:
    def test_department_id_set_on_accept(self) -> None:
        t = _make_ticket(status=TicketStatus.TRIAGE)
        dept = uuid4()
        t.accept_triage(actor=_ACTOR, department_id=dept, now=_NOW)
        assert t.department_id == dept

    def test_department_id_none_before_triage(self) -> None:
        t = Ticket.create(author_id=uuid4(), title="T", now=_NOW)
        assert t.department_id is None


# ===========================================================================
# Terminal status — closed_at set
# ===========================================================================


class TestTerminalStatuses:
    def test_closed_sets_closed_at(self) -> None:
        t = _make_ticket(status=TicketStatus.ACCEPTANCE)
        t.close(actor=_ACTOR, now=_NOW)
        assert t.closed_at == _NOW

    def test_rejected_from_triage_sets_closed_at(self) -> None:
        t = _make_ticket(status=TicketStatus.TRIAGE)
        t.reject(actor=_ACTOR, comment="duplicate", now=_NOW)
        assert t.closed_at == _NOW

    def test_rejected_from_acceptance_sets_closed_at(self) -> None:
        t = _make_ticket(status=TicketStatus.ACCEPTANCE)
        t.reject(actor=_ACTOR, comment="value gate failed", now=_NOW)
        assert t.closed_at == _NOW


# ===========================================================================
# StatusTransition record
# ===========================================================================


class TestStatusTransitionRecord:
    def test_transition_contains_ticket_id(self) -> None:
        t = _make_ticket(status=TicketStatus.CREATED)
        tr = t.submit(actor=_ACTOR, now=_NOW)
        assert tr.ticket_id == t.id

    def test_transition_has_unique_id(self) -> None:
        t1 = _make_ticket(status=TicketStatus.CREATED)
        t2 = _make_ticket(status=TicketStatus.CREATED)
        tr1 = t1.submit(actor=_ACTOR, now=_NOW)
        tr2 = t2.submit(actor=_ACTOR, now=_NOW)
        assert tr1.id != tr2.id

    def test_transition_acceptance_cycle_matches_ticket(self) -> None:
        t = _make_ticket(status=TicketStatus.CREATED, acceptance_cycle=1)
        tr = t.submit(actor=_ACTOR, now=_NOW)
        assert tr.acceptance_cycle == 1


# ===========================================================================
# GateAttestation invariants
# ===========================================================================


class TestGateAttestationInvariants:
    def _make_attestation(
        self, *, kind: AttestationKind, cycle: int, spec_ref: str | None = None
    ) -> GateAttestation:
        return GateAttestation(
            id=uuid4(),
            ticket_id=uuid4(),
            kind=kind,
            acceptance_cycle=cycle,
            attested_by="sub-1",
            roles_snapshot=["ba"],
            checklist=None,
            spec_ref=spec_ref,
            agreed_with_subject=None,
            comment=None,
            attested_at=_NOW,
        )

    def test_spec_approved_must_have_cycle_0(self) -> None:
        with pytest.raises(TicketValidationError):
            self._make_attestation(kind=AttestationKind.SPEC_APPROVED, cycle=1, spec_ref="REF-1")

    def test_spec_approved_cycle_0_is_valid(self) -> None:
        a = self._make_attestation(kind=AttestationKind.SPEC_APPROVED, cycle=0, spec_ref="REF-1")
        assert a.acceptance_cycle == 0

    def test_spec_approved_requires_spec_ref(self) -> None:
        with pytest.raises(TicketValidationError):
            self._make_attestation(kind=AttestationKind.SPEC_APPROVED, cycle=0, spec_ref=None)

    def test_formal_dod_requires_cycle_ge_1(self) -> None:
        with pytest.raises(TicketValidationError):
            self._make_attestation(kind=AttestationKind.FORMAL_DOD, cycle=0)

    def test_formal_dod_cycle_1_is_valid(self) -> None:
        a = self._make_attestation(kind=AttestationKind.FORMAL_DOD, cycle=1)
        assert a.acceptance_cycle == 1

    def test_business_value_requires_cycle_ge_1(self) -> None:
        with pytest.raises(TicketValidationError):
            self._make_attestation(kind=AttestationKind.BUSINESS_VALUE, cycle=0)


# ===========================================================================
# TriageDecision invariants
# ===========================================================================


class TestTriageDecisionInvariants:
    def _make_decision(
        self,
        *,
        outcome: TriageOutcome,
        department_id: UUID | None = None,
        rejection_reason: RejectionReason | None = None,
        duplicate_of_ticket_id: UUID | None = None,
    ) -> TriageDecision:
        return TriageDecision(
            id=uuid4(),
            ticket_id=uuid4(),
            outcome=outcome,
            department_id=department_id,
            rejection_reason=rejection_reason,
            duplicate_of_ticket_id=duplicate_of_ticket_id,
            comment=None,
            decided_by="sub-1",
            decided_at=_NOW,
        )

    def test_accepted_requires_department_id(self) -> None:
        with pytest.raises(AssignmentMissingError):
            self._make_decision(outcome=TriageOutcome.ACCEPTED, department_id=None)

    def test_accepted_with_department_id_is_valid(self) -> None:
        d = self._make_decision(outcome=TriageOutcome.ACCEPTED, department_id=uuid4())
        assert d.department_id is not None

    def test_rejected_requires_rejection_reason(self) -> None:
        with pytest.raises(AssignmentMissingError):
            self._make_decision(outcome=TriageOutcome.REJECTED, rejection_reason=None)

    def test_rejected_duplicate_requires_duplicate_id(self) -> None:
        with pytest.raises(AssignmentMissingError):
            self._make_decision(
                outcome=TriageOutcome.REJECTED,
                rejection_reason=RejectionReason.DUPLICATE,
                duplicate_of_ticket_id=None,
            )

    def test_rejected_duplicate_with_id_is_valid(self) -> None:
        d = self._make_decision(
            outcome=TriageOutcome.REJECTED,
            rejection_reason=RejectionReason.DUPLICATE,
            duplicate_of_ticket_id=uuid4(),
        )
        assert d.duplicate_of_ticket_id is not None

    def test_returned_has_no_invariant_constraints(self) -> None:
        # TriageOutcome.RETURNED has no mandatory fields beyond the basics
        d = self._make_decision(outcome=TriageOutcome.RETURNED)
        assert d.outcome is TriageOutcome.RETURNED


# ===========================================================================
# Assignment.is_active
# ===========================================================================


class TestAssignmentIsActive:
    def test_active_when_unassigned_at_is_none(self) -> None:
        a = Assignment(
            id=uuid4(),
            ticket_id=uuid4(),
            role=AssignmentRole.BUSINESS_OWNER,
            subject="sub-1",
            assigned_by="admin",
            assigned_at=_NOW,
            unassigned_at=None,
        )
        assert a.is_active is True

    def test_inactive_when_unassigned_at_is_set(self) -> None:
        a = Assignment(
            id=uuid4(),
            ticket_id=uuid4(),
            role=AssignmentRole.EXECUTOR,
            subject="sub-2",
            assigned_by="admin",
            assigned_at=_NOW,
            unassigned_at=_NOW,
        )
        assert a.is_active is False
