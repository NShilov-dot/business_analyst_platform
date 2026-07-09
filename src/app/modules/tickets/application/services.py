"""Ticket application service.

Orchestrates domain logic, enforces per-object access rights, calls port
guards, and emits exactly one domain event per mutation.

PII discipline: events carry keys/statuses/IDs — never free-text field values
from the intake submission payload.

REQUIRED field: ``publisher: EventPublisher``.  Per NoopPublisher docstring,
missing publisher wiring is a loud failure; tests inject NoopPublisher or a
recording fake explicitly.

TrackerPort calls are best-effort — exceptions are swallowed and logged
(Postgres is always the system of record).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.core.events import EventPublisher
from app.modules.intake_templates.domain.entities import FREE_FORM_VERSION_ID
from app.modules.intake_templates.domain.ports import SubmissionValidator
from app.modules.tickets.application.dtos import (
    AssignCommand,
    AttestBusinessValueCommand,
    AttestFormalDodCommand,
    AttestSpecApprovalCommand,
    CloseCommand,
    CreateTicketCommand,
    FinishWorkCommand,
    IntakeShareQuery,
    IntakeShareRow,
    IntakeShareStats,
    ListTicketsQuery,
    RejectCommand,
    ReplaceSubmissionCommand,
    RequestAcceptanceCommand,
    ReturnForRefinementCommand,
    ReturnToWorkCommand,
    StartWorkCommand,
    SubmitCommand,
    TicketDetail,
    TicketPage,
    TriageAcceptCommand,
    UpdateTicketCommand,
)
from app.modules.tickets.domain.entities import (
    Assignment,
    AssignmentRole,
    AttestationKind,
    GateAttestation,
    IntakeSubmission,
    RejectionReason,
    StatusTransition,
    Ticket,
    TicketStatus,
    TriageDecision,
    TriageOutcome,
)
from app.modules.tickets.domain.errors import (
    AcceptanceGateIncompleteError,
    AssignmentMissingError,
    AttestationDuplicateError,
    AttestationNotAllowedError,
    SpecNotApprovedError,
    TicketAccessDeniedError,
    TicketNotFoundError,
    TicketTransitionForbiddenError,
    TicketValidationError,
    TriageDecisionInvalidError,
)
from app.modules.tickets.domain.ports import (
    AcceptanceGate,
    DepartmentLookup,
    SpecApprovalGate,
    TemplateVersionInfo,
    TicketRepository,
    TrackerPort,
)

log = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Role constants — ANY-of checks
# ---------------------------------------------------------------------------

_BA_ROLES: frozenset[str] = frozenset({"ba"})
_ADMIN_ROLES: frozenset[str] = frozenset({"tenant_admin", "platform_admin"})
_BA_OR_ADMIN: frozenset[str] = _BA_ROLES | _ADMIN_ROLES


def _is_ba(roles: frozenset[str]) -> bool:
    return bool(roles & _BA_ROLES)


def _is_admin(roles: frozenset[str]) -> bool:
    return bool(roles & _ADMIN_ROLES)


def _is_ba_or_admin(roles: frozenset[str]) -> bool:
    return bool(roles & _BA_OR_ADMIN)


# ---------------------------------------------------------------------------
# TicketService
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TicketService:
    """All mutations emit exactly one domain event via ``publisher``.

    ``publisher`` is a REQUIRED field — no silent no-op default (see
    NoopPublisher docstring in core.events).

    ``tracker`` calls are best-effort: failures are logged and do not block
    the local transaction.
    """

    repo: TicketRepository
    validator: SubmissionValidator
    dept_lookup: DepartmentLookup
    version_info: TemplateVersionInfo
    spec_gate: SpecApprovalGate
    acceptance_gate: AcceptanceGate
    tracker: TrackerPort
    publisher: EventPublisher
    clock: Callable[[], datetime] = field(default=_utc_now)

    # ================================================================== #
    # Create                                                              #
    # ================================================================== #

    async def create_ticket(
        self,
        *,
        actor_id: UUID,
        actor_sub: str,
        roles: frozenset[str],
        command: CreateTicketCommand,
    ) -> tuple[Ticket, IntakeSubmission, StatusTransition]:
        """Create a draft ticket (status=created) with submission version 1.

        Any authenticated user may create a ticket.
        Template version must be PUBLISHED (TemplateVersionInfo gate — closes
        the confirmed gap that validate_submission does not check status).
        The draft may be structurally incomplete — full validation is on submit.
        """
        now = self.clock()

        # Gate: template version must be published
        if not await self.version_info.is_published_version(command.template_version_id):
            raise TicketValidationError(
                f"Template version {command.template_version_id} is not published; "
                "only published versions may be used for new submissions"
            )

        ticket = Ticket.create(
            author_id=actor_id,
            title=command.title,
            description=command.description,
            priority=command.priority,
            now=now,
        )
        await self.repo.add_ticket(ticket)

        submission = IntakeSubmission(
            id=uuid4(),
            ticket_id=ticket.id,
            version=1,
            template_version_id=command.template_version_id,
            payload=command.payload,
            created_by=actor_sub,
            created_at=now,
        )
        await self.repo.add_submission(submission)

        transition = StatusTransition(
            id=uuid4(),
            ticket_id=ticket.id,
            from_status=None,
            to_status=TicketStatus.CREATED,
            actor=actor_sub,
            comment=None,
            acceptance_cycle=ticket.acceptance_cycle,
            occurred_at=now,
        )
        await self.repo.add_transition(transition)

        await self.publisher(
            "ticket",
            ticket.id,
            "created",
            after={
                "status": ticket.status,
                "template_version_id": str(submission.template_version_id),
                "submission_version": submission.version,
            },
        )
        return ticket, submission, transition

    # ================================================================== #
    # Read                                                                #
    # ================================================================== #

    async def get_ticket(self, *, ticket_id: UUID) -> TicketDetail:
        """Return full ticket detail (any authenticated member)."""
        ticket = await self._load_or_404(ticket_id)
        current_submission = await self.repo.get_current_submission(ticket_id)
        assignments = [
            a
            for role in AssignmentRole
            if (a := await self.repo.get_active_assignment(ticket_id, role)) is not None
        ]
        attestations = await self.repo.list_attestations(ticket_id)
        triage_decisions = await self.repo.list_triage_decisions(ticket_id)
        return TicketDetail(
            ticket=ticket,
            current_submission=current_submission,
            assignments=assignments,
            attestations=attestations,
            triage_decisions=triage_decisions,
        )

    async def list_tickets(
        self,
        *,
        actor_id: UUID,
        actor_sub: str,
        query: ListTicketsQuery,
    ) -> TicketPage:
        """Paginated list of tickets (any authenticated member)."""
        items, total = await self.repo.list_tickets(
            status=query.status,
            department_id=query.department_id,
            author_id=actor_id if query.mine else None,
            assigned_to=actor_sub if query.assigned_to_me else None,
            limit=query.limit,
            offset=query.offset,
        )
        return TicketPage(items=items, total=total, limit=query.limit, offset=query.offset)

    async def list_transitions(self, *, ticket_id: UUID) -> list[StatusTransition]:
        """Append-only history of status transitions (any authenticated member)."""
        await self._load_or_404(ticket_id)
        return await self.repo.list_transitions(ticket_id)

    async def list_attestations(self, *, ticket_id: UUID) -> list[GateAttestation]:
        """Gate attestation records by cycle (any authenticated member)."""
        await self._load_or_404(ticket_id)
        return await self.repo.list_attestations(ticket_id)

    async def intake_share_stats(
        self,
        *,
        roles: frozenset[str],
        query: IntakeShareQuery,
    ) -> IntakeShareStats:
        """intake-share metric (ba or admins)."""
        if not _is_ba_or_admin(roles):
            raise TicketAccessDeniedError("intake-share stats require ba or admin role")
        rows = await self.repo.intake_share(
            created_from=query.created_from,
            created_to=query.created_to,
        )
        total = sum(count for _, count in rows)
        free_form = sum(
            count for version_id, count in rows if version_id == FREE_FORM_VERSION_ID
        )
        templated = total - free_form
        share = templated / total if total > 0 else 0.0
        by_version = [IntakeShareRow(template_version_id=vid, count=cnt) for vid, cnt in rows]
        return IntakeShareStats(
            total=total,
            free_form=free_form,
            templated=templated,
            templated_share=share,
            by_template_version=by_version,
        )

    # ================================================================== #
    # Metadata update                                                     #
    # ================================================================== #

    async def update_ticket(
        self,
        *,
        ticket_id: UUID,
        actor_id: UUID,
        actor_sub: str,
        roles: frozenset[str],
        command: UpdateTicketCommand,
    ) -> Ticket:
        """PATCH ticket metadata (author or ba or admins; only in {created, triage})."""
        ticket = await self._load_or_404(ticket_id)
        self._assert_can_manage_ticket(ticket, actor_id=actor_id, roles=roles)

        now = self.clock()
        before_status = ticket.status
        ticket.update_details(
            title=command.title,
            description=command.description if command.description_set else None,
            set_description=command.description_set,
            priority=command.priority if command.priority_set else None,
            set_priority=command.priority_set,
            now=now,
        )
        await self.repo.update_ticket(ticket)

        await self.publisher(
            "ticket",
            ticket_id,
            "updated",
            before={"status": before_status},
            after={"status": ticket.status},
        )
        return ticket

    # ================================================================== #
    # Submission replacement                                              #
    # ================================================================== #

    async def replace_submission(
        self,
        *,
        ticket_id: UUID,
        actor_id: UUID,
        actor_sub: str,
        roles: frozenset[str],
        command: ReplaceSubmissionCommand,
    ) -> IntakeSubmission:
        """PUT /tickets/{id}/submission — new version (only while status == created)."""
        ticket = await self._load_or_404(ticket_id)
        self._assert_can_manage_ticket(ticket, actor_id=actor_id, roles=roles)

        # Domain guard: raises TicketSubmissionFrozenError if not created
        ticket.assert_submission_editable()

        # Gate: template version must be published
        if not await self.version_info.is_published_version(command.template_version_id):
            raise TicketValidationError(
                f"Template version {command.template_version_id} is not published"
            )

        # Determine next version number
        existing = await self.repo.list_submissions(ticket_id)
        next_version = max((s.version for s in existing), default=0) + 1

        now = self.clock()
        submission = IntakeSubmission(
            id=uuid4(),
            ticket_id=ticket_id,
            version=next_version,
            template_version_id=command.template_version_id,
            payload=command.payload,
            created_by=actor_sub,
            created_at=now,
        )
        await self.repo.add_submission(submission)

        await self.publisher(
            "ticket",
            ticket_id,
            "submission_replaced",
            after={
                "submission_version": submission.version,
                "template_version_id": str(submission.template_version_id),
            },
        )
        return submission

    # ================================================================== #
    # Submit (created → triage)                                           #
    # ================================================================== #

    async def submit(
        self,
        *,
        ticket_id: UUID,
        actor_id: UUID,
        actor_sub: str,
        roles: frozenset[str],
        command: SubmitCommand,
    ) -> tuple[Ticket, StatusTransition]:
        """Edge 1: created → triage.

        Author or ba or admins.  SubmissionValidator must return [].
        """
        ticket = await self._load_or_404(ticket_id)
        self._assert_can_manage_ticket(ticket, actor_id=actor_id, roles=roles)

        # Validate current submission
        current_submission = await self.repo.get_current_submission(ticket_id)
        if current_submission is None:
            raise TicketValidationError("Cannot submit a ticket with no intake submission")

        errors = await self.validator.validate_submission(
            template_version_id=current_submission.template_version_id,
            payload=current_submission.payload,
        )
        if errors:
            from app.modules.tickets.domain.errors import SubmissionInvalidError

            raise SubmissionInvalidError(
                "Submission has validation errors",
                field_errors=[{"key": e.key, "message": e.message} for e in errors],
            )

        now = self.clock()
        transition = ticket.submit(actor=actor_sub, now=now)
        await self.repo.update_ticket(ticket)
        await self.repo.add_transition(transition)

        await self.publisher(
            "ticket",
            ticket_id,
            "submitted",
            before={"status": TicketStatus.CREATED},
            after={"status": TicketStatus.TRIAGE},
        )
        return ticket, transition

    # ================================================================== #
    # Triage — accept (triage → spec_approval)                           #
    # ================================================================== #

    async def triage_accept(
        self,
        *,
        ticket_id: UUID,
        actor_sub: str,
        roles: frozenset[str],
        command: TriageAcceptCommand,
    ) -> tuple[Ticket, TriageDecision, StatusTransition]:
        """Edge 2: triage → spec_approval.

        ba or admins.  Atomically: sets department_id, creates assignment(s),
        records TriageDecision, transitions status.
        """
        if not _is_ba_or_admin(roles):
            raise TicketAccessDeniedError("triage-accept requires ba or admin role")

        ticket = await self._load_or_404(ticket_id)

        # Department must exist and be active
        if not await self.dept_lookup.is_active_department(command.department_id):
            raise TriageDecisionInvalidError(
                f"Department {command.department_id} does not exist or is not active"
            )

        now = self.clock()

        # Assign business_owner (mandatory at triage accept)
        await self._replace_assignment(
            ticket_id=ticket_id,
            role=AssignmentRole.BUSINESS_OWNER,
            subject=command.business_owner_subject,
            assigned_by=actor_sub,
            now=now,
        )

        # Optionally assign executor
        if command.executor_subject:
            await self._replace_assignment(
                ticket_id=ticket_id,
                role=AssignmentRole.EXECUTOR,
                subject=command.executor_subject,
                assigned_by=actor_sub,
                now=now,
            )

        # Update priority if provided
        if command.priority is not None:
            ticket.update_details(priority=command.priority, set_priority=True, now=now)

        # Record triage decision (domain invariants enforced by TriageDecision.__post_init__)
        decision = TriageDecision(
            id=uuid4(),
            ticket_id=ticket_id,
            outcome=TriageOutcome.ACCEPTED,
            department_id=command.department_id,
            rejection_reason=None,
            duplicate_of_ticket_id=None,
            comment=command.comment,
            decided_by=actor_sub,
            decided_at=now,
        )
        await self.repo.add_triage_decision(decision)

        # Transition
        transition = ticket.accept_triage(
            actor=actor_sub,
            department_id=command.department_id,
            now=now,
        )
        await self.repo.update_ticket(ticket)
        await self.repo.add_transition(transition)

        await self.publisher(
            "ticket",
            ticket_id,
            "triage_accepted",
            before={"status": TicketStatus.TRIAGE},
            after={
                "status": TicketStatus.SPEC_APPROVAL,
                "department_id": str(command.department_id),
            },
        )
        return ticket, decision, transition

    # ================================================================== #
    # Return for refinement (triage → created)                           #
    # ================================================================== #

    async def return_for_refinement(
        self,
        *,
        ticket_id: UUID,
        actor_sub: str,
        roles: frozenset[str],
        command: ReturnForRefinementCommand,
    ) -> tuple[Ticket, TriageDecision, StatusTransition]:
        """Edge 3 [PO]: triage → created.  ba or admins.  Comment mandatory."""
        if not _is_ba_or_admin(roles):
            raise TicketAccessDeniedError("return-for-refinement requires ba or admin role")

        ticket = await self._load_or_404(ticket_id)
        now = self.clock()

        decision = TriageDecision(
            id=uuid4(),
            ticket_id=ticket_id,
            outcome=TriageOutcome.RETURNED,
            department_id=None,
            rejection_reason=None,
            duplicate_of_ticket_id=None,
            comment=command.comment,
            decided_by=actor_sub,
            decided_at=now,
        )
        await self.repo.add_triage_decision(decision)

        transition = ticket.return_for_refinement(actor=actor_sub, comment=command.comment, now=now)
        await self.repo.update_ticket(ticket)
        await self.repo.add_transition(transition)

        await self.publisher(
            "ticket",
            ticket_id,
            "returned_for_refinement",
            before={"status": TicketStatus.TRIAGE},
            after={"status": TicketStatus.CREATED},
        )
        return ticket, decision, transition

    # ================================================================== #
    # Reject (triage → rejected or acceptance → rejected)                #
    # ================================================================== #

    async def reject(
        self,
        *,
        ticket_id: UUID,
        actor_sub: str,
        roles: frozenset[str],
        command: RejectCommand,
    ) -> tuple[Ticket, TriageDecision | None, StatusTransition]:
        """Edges 4 (triage → rejected) and 10 (acceptance → rejected).

        ba or admins.  Comment mandatory.  For triage rejection, rejection_reason
        is mandatory.
        """
        if not _is_ba_or_admin(roles):
            raise TicketAccessDeniedError("reject requires ba or admin role")

        ticket = await self._load_or_404(ticket_id)
        now = self.clock()
        from_status = ticket.status

        triage_decision: TriageDecision | None = None

        if from_status == TicketStatus.TRIAGE:
            if command.rejection_reason is None:
                raise TriageDecisionInvalidError(
                    "rejection_reason is required when rejecting from triage"
                )
            if (
                command.rejection_reason == RejectionReason.DUPLICATE
                and command.duplicate_of_ticket_id is None
            ):
                raise TriageDecisionInvalidError(
                    "duplicate_of_ticket_id is required when rejection_reason == duplicate"
                )
            if (
                command.rejection_reason == RejectionReason.DUPLICATE
                and command.duplicate_of_ticket_id == ticket_id
            ):
                raise TriageDecisionInvalidError(
                    "duplicate_of_ticket_id must not be the same ticket"
                )

            triage_decision = TriageDecision(
                id=uuid4(),
                ticket_id=ticket_id,
                outcome=TriageOutcome.REJECTED,
                department_id=None,
                rejection_reason=command.rejection_reason,
                duplicate_of_ticket_id=command.duplicate_of_ticket_id,
                comment=command.comment,
                decided_by=actor_sub,
                decided_at=now,
            )
            await self.repo.add_triage_decision(triage_decision)

        transition = ticket.reject(actor=actor_sub, comment=command.comment, now=now)
        await self.repo.update_ticket(ticket)
        await self.repo.add_transition(transition)

        await self.publisher(
            "ticket",
            ticket_id,
            "rejected",
            before={"status": from_status},
            after={"status": TicketStatus.REJECTED},
        )
        return ticket, triage_decision, transition

    # ================================================================== #
    # Assign / reassign                                                   #
    # ================================================================== #

    async def assign(
        self,
        *,
        ticket_id: UUID,
        actor_sub: str,
        roles: frozenset[str],
        command: AssignCommand,
    ) -> Assignment:
        """Change business_owner or executor assignment (ba or admins).

        Raises TicketTransitionForbiddenError if ticket is terminal (no more
        assignments make sense on closed/rejected tickets).
        """
        if not _is_ba_or_admin(roles):
            raise TicketAccessDeniedError("assign requires ba or admin role")

        ticket = await self._load_or_404(ticket_id)
        if ticket.status in {TicketStatus.CLOSED, TicketStatus.REJECTED}:
            raise TicketTransitionForbiddenError(
                "Cannot assign roles on a terminal ticket"
            )

        now = self.clock()
        assignment = await self._replace_assignment(
            ticket_id=ticket_id,
            role=command.role,
            subject=command.subject,
            assigned_by=actor_sub,
            now=now,
        )

        await self.publisher(
            "ticket",
            ticket_id,
            "assignment_changed",
            after={
                "role": command.role,
                "subject": command.subject,
                "status": ticket.status,
            },
        )
        return assignment

    # ================================================================== #
    # Attest: spec-approval (spec_approval status only)                  #
    # ================================================================== #

    async def attest_spec_approval(
        self,
        *,
        ticket_id: UUID,
        actor_sub: str,
        roles: frozenset[str],
        command: AttestSpecApprovalCommand,
    ) -> GateAttestation:
        """Record spec_approved attestation.

        ONLY the ba role — no admin fallback.
        Status must be spec_approval.
        agreed_with_subject must match the active business_owner.
        """
        if not _is_ba(roles):
            raise AttestationNotAllowedError(
                "Only the ba role may sign spec_approved attestation; no admin fallback"
            )

        ticket = await self._load_or_404(ticket_id)
        if ticket.status != TicketStatus.SPEC_APPROVAL:
            raise TicketTransitionForbiddenError(
                f"spec_approved attestation requires status == spec_approval (was {ticket.status!s})"
            )

        # agreed_with_subject must equal active business_owner
        bo = await self.repo.get_active_assignment(ticket_id, AssignmentRole.BUSINESS_OWNER)
        if bo is None:
            raise AssignmentMissingError(
                "No active business_owner assignment — cannot attest spec approval"
            )
        if command.agreed_with_subject != bo.subject:
            raise AttestationNotAllowedError(
                "agreed_with_subject must equal the active business_owner subject"
            )

        # Duplicate check
        existing = await self.repo.get_attestation(ticket_id, AttestationKind.SPEC_APPROVED, 0)
        if existing is not None:
            raise AttestationDuplicateError(
                "A spec_approved attestation (cycle 0) already exists for this ticket"
            )

        # In this product the ticket itself is the ТЗ. When no external spec
        # reference is supplied, pin a self-reference to the ticket and the exact
        # intake-submission version being approved — so the signed attestation
        # still records precisely WHAT was approved (satisfies the spec_ref invariant).
        spec_ref = command.spec_ref
        if not spec_ref:
            submission = await self.repo.get_current_submission(ticket_id)
            spec_ref = (
                f"ticket:{ticket_id}#submission-v{submission.version}"
                if submission is not None
                else f"ticket:{ticket_id}"
            )

        now = self.clock()
        attestation = GateAttestation(
            id=uuid4(),
            ticket_id=ticket_id,
            kind=AttestationKind.SPEC_APPROVED,
            acceptance_cycle=0,
            attested_by=actor_sub,
            roles_snapshot=command.roles_snapshot or sorted(roles),
            checklist=None,
            spec_ref=spec_ref,
            agreed_with_subject=command.agreed_with_subject,
            comment=command.comment,
            attested_at=now,
        )
        await self.repo.add_attestation(attestation)

        await self.publisher(
            "ticket",
            ticket_id,
            "spec_approval_attested",
            after={
                "kind": AttestationKind.SPEC_APPROVED,
                "attestation_id": str(attestation.id),
                "cycle": 0,
                "status": ticket.status,
            },
        )
        return attestation

    # ================================================================== #
    # Start work (spec_approval → in_progress)                           #
    # ================================================================== #

    async def start_work(
        self,
        *,
        ticket_id: UUID,
        actor_id: UUID,
        actor_sub: str,
        roles: frozenset[str],
        command: StartWorkCommand,
    ) -> tuple[Ticket, StatusTransition]:
        """Edge 5: spec_approval → in_progress.

        ba or executor (per-object) or admins.
        Guards: SpecApprovalGate + active executor assignment.
        TrackerPort.mirror_create is best-effort after the transition.
        """
        ticket = await self._load_or_404(ticket_id)

        # Per-object: executor check for role gate
        executor = await self.repo.get_active_assignment(ticket_id, AssignmentRole.EXECUTOR)
        is_executor = executor is not None and executor.subject == actor_sub
        if not (_is_ba_or_admin(roles) or is_executor):
            raise TicketAccessDeniedError(
                "start-work requires ba, admin, or active executor role"
            )

        # Guard 1: SpecApprovalGate
        gate_result = await self.spec_gate.is_satisfied(ticket_id)
        if not gate_result.ok:
            raise SpecNotApprovedError(
                "Spec approval gate is not satisfied",
                reasons=list(gate_result.reasons),
            )

        # Guard 2: active executor must exist
        if executor is None:
            raise AssignmentMissingError(
                "An active executor assignment is required before starting work"
            )

        now = self.clock()
        transition = ticket.start_work(actor=actor_sub, now=now)
        await self.repo.update_ticket(ticket)
        await self.repo.add_transition(transition)

        # Best-effort mirror
        try:
            await self.tracker.mirror_create(ticket)
        except Exception:
            log.warning("TrackerPort.mirror_create failed for ticket %s", ticket_id, exc_info=True)

        await self.publisher(
            "ticket",
            ticket_id,
            "work_started",
            before={"status": TicketStatus.SPEC_APPROVAL},
            after={"status": TicketStatus.IN_PROGRESS},
        )
        return ticket, transition

    # ================================================================== #
    # Finish work (in_progress → change_capture)                         #
    # ================================================================== #

    async def finish_work(
        self,
        *,
        ticket_id: UUID,
        actor_id: UUID,
        actor_sub: str,
        roles: frozenset[str],
        command: FinishWorkCommand,
    ) -> tuple[Ticket, StatusTransition]:
        """Edge 6: in_progress → change_capture.

        executor (per-object) or ba or admins.
        This is the only SYSTEM_ACTOR_ALLOWED_EDGES edge (webhook path in MVP).
        """
        ticket = await self._load_or_404(ticket_id)

        executor = await self.repo.get_active_assignment(ticket_id, AssignmentRole.EXECUTOR)
        is_executor = executor is not None and executor.subject == actor_sub
        is_system = actor_sub == "system:tracker"

        if not (_is_ba_or_admin(roles) or is_executor or is_system):
            raise TicketAccessDeniedError(
                "finish-work requires executor (per-object), ba, admin, or system:tracker"
            )

        now = self.clock()
        transition = ticket.finish_work(actor=actor_sub, now=now)
        await self.repo.update_ticket(ticket)
        await self.repo.add_transition(transition)

        await self.publisher(
            "ticket",
            ticket_id,
            "work_finished",
            before={"status": TicketStatus.IN_PROGRESS},
            after={"status": TicketStatus.CHANGE_CAPTURE},
        )
        return ticket, transition

    # ================================================================== #
    # Request acceptance (change_capture → acceptance)                   #
    # ================================================================== #

    async def request_acceptance(
        self,
        *,
        ticket_id: UUID,
        actor_id: UUID,
        actor_sub: str,
        roles: frozenset[str],
        command: RequestAcceptanceCommand,
    ) -> tuple[Ticket, StatusTransition]:
        """Edge 7: change_capture → acceptance.

        executor (per-object) or ba or admins.  changes_summary mandatory.
        """
        ticket = await self._load_or_404(ticket_id)

        executor = await self.repo.get_active_assignment(ticket_id, AssignmentRole.EXECUTOR)
        is_executor = executor is not None and executor.subject == actor_sub
        if not (_is_ba_or_admin(roles) or is_executor):
            raise TicketAccessDeniedError(
                "request-acceptance requires executor (per-object), ba, or admin"
            )

        now = self.clock()
        transition = ticket.request_acceptance(
            actor=actor_sub, changes_summary=command.changes_summary, now=now
        )
        await self.repo.update_ticket(ticket)
        await self.repo.add_transition(transition)

        await self.publisher(
            "ticket",
            ticket_id,
            "acceptance_requested",
            before={"status": TicketStatus.CHANGE_CAPTURE},
            after={"status": TicketStatus.ACCEPTANCE},
        )
        return ticket, transition

    # ================================================================== #
    # Attest: formal DoD (acceptance status only)                        #
    # ================================================================== #

    async def attest_formal_dod(
        self,
        *,
        ticket_id: UUID,
        actor_sub: str,
        roles: frozenset[str],
        command: AttestFormalDodCommand,
    ) -> GateAttestation:
        """Record formal_dod attestation.

        ONLY the ba role — no admin fallback.
        Status must be acceptance.
        All checklist values must be True.
        """
        if not _is_ba(roles):
            raise AttestationNotAllowedError(
                "Only the ba role may sign formal_dod attestation; no admin fallback"
            )

        ticket = await self._load_or_404(ticket_id)
        if ticket.status != TicketStatus.ACCEPTANCE:
            raise TicketTransitionForbiddenError(
                f"formal_dod attestation requires status == acceptance (was {ticket.status!s})"
            )

        # All checklist items must be True
        if not command.checklist:
            raise TicketValidationError("formal_dod checklist cannot be empty")
        if not all(command.checklist.values()):
            failing = [k for k, v in command.checklist.items() if not v]
            raise TicketValidationError(
                f"All formal_dod checklist items must be True; failing: {failing}"
            )

        cycle = ticket.acceptance_cycle
        existing = await self.repo.get_attestation(ticket_id, AttestationKind.FORMAL_DOD, cycle)
        if existing is not None:
            raise AttestationDuplicateError(
                f"A formal_dod attestation for cycle {cycle} already exists"
            )

        now = self.clock()
        attestation = GateAttestation(
            id=uuid4(),
            ticket_id=ticket_id,
            kind=AttestationKind.FORMAL_DOD,
            acceptance_cycle=cycle,
            attested_by=actor_sub,
            roles_snapshot=command.roles_snapshot or sorted(roles),
            checklist=command.checklist,
            spec_ref=None,
            agreed_with_subject=None,
            comment=command.comment,
            attested_at=now,
        )
        await self.repo.add_attestation(attestation)

        await self.publisher(
            "ticket",
            ticket_id,
            "formal_dod_attested",
            after={
                "kind": AttestationKind.FORMAL_DOD,
                "attestation_id": str(attestation.id),
                "cycle": cycle,
                "status": ticket.status,
            },
        )
        return attestation

    # ================================================================== #
    # Attest: business value (acceptance status only)                    #
    # ================================================================== #

    async def attest_business_value(
        self,
        *,
        ticket_id: UUID,
        actor_sub: str,
        roles: frozenset[str],
        command: AttestBusinessValueCommand,
    ) -> GateAttestation:
        """Record business_value attestation.

        ONLY the active business_owner subject — NO role fallback, no admin override.
        """
        ticket = await self._load_or_404(ticket_id)
        if ticket.status != TicketStatus.ACCEPTANCE:
            raise TicketTransitionForbiddenError(
                f"business_value attestation requires status == acceptance (was {ticket.status!s})"
            )

        # Strict per-object check: caller must be the active business_owner
        bo = await self.repo.get_active_assignment(ticket_id, AssignmentRole.BUSINESS_OWNER)
        if bo is None or bo.subject != actor_sub:
            raise AttestationNotAllowedError(
                "Only the active business_owner subject may sign business_value attestation; "
                "no role fallback or admin override"
            )

        cycle = ticket.acceptance_cycle
        existing = await self.repo.get_attestation(
            ticket_id, AttestationKind.BUSINESS_VALUE, cycle
        )
        if existing is not None:
            raise AttestationDuplicateError(
                f"A business_value attestation for cycle {cycle} already exists"
            )

        now = self.clock()
        attestation = GateAttestation(
            id=uuid4(),
            ticket_id=ticket_id,
            kind=AttestationKind.BUSINESS_VALUE,
            acceptance_cycle=cycle,
            attested_by=actor_sub,
            roles_snapshot=list(roles),
            checklist=None,
            spec_ref=None,
            agreed_with_subject=None,
            comment=command.comment,
            attested_at=now,
        )
        await self.repo.add_attestation(attestation)

        await self.publisher(
            "ticket",
            ticket_id,
            "business_value_attested",
            after={
                "kind": AttestationKind.BUSINESS_VALUE,
                "attestation_id": str(attestation.id),
                "cycle": cycle,
                "status": ticket.status,
            },
        )
        return attestation

    # ================================================================== #
    # Return to work (acceptance → in_progress)                          #
    # ================================================================== #

    async def return_to_work(
        self,
        *,
        ticket_id: UUID,
        actor_sub: str,
        roles: frozenset[str],
        command: ReturnToWorkCommand,
    ) -> tuple[Ticket, StatusTransition]:
        """Edge 9 [PO]: acceptance → in_progress.

        ba or business_owner (per-object) or admins.  Comment mandatory.
        acceptance_cycle is incremented.
        """
        ticket = await self._load_or_404(ticket_id)

        bo = await self.repo.get_active_assignment(ticket_id, AssignmentRole.BUSINESS_OWNER)
        is_bo = bo is not None and bo.subject == actor_sub
        if not (_is_ba_or_admin(roles) or is_bo):
            raise TicketAccessDeniedError(
                "return-to-work requires ba, admin, or active business_owner (per-object)"
            )

        old_cycle = ticket.acceptance_cycle
        now = self.clock()
        transition = ticket.return_to_work(actor=actor_sub, comment=command.comment, now=now)
        await self.repo.update_ticket(ticket)
        await self.repo.add_transition(transition)

        await self.publisher(
            "ticket",
            ticket_id,
            "returned_to_work",
            before={"status": TicketStatus.ACCEPTANCE, "acceptance_cycle": old_cycle},
            after={"status": TicketStatus.IN_PROGRESS, "acceptance_cycle": ticket.acceptance_cycle},
        )
        return ticket, transition

    # ================================================================== #
    # Close (acceptance → closed)                                        #
    # ================================================================== #

    async def close(
        self,
        *,
        ticket_id: UUID,
        actor_sub: str,
        roles: frozenset[str],
        command: CloseCommand,
    ) -> tuple[Ticket, StatusTransition]:
        """Edge 8: acceptance → closed.

        ba or admins.  AcceptanceGate must be satisfied for the CURRENT cycle.
        """
        if not _is_ba_or_admin(roles):
            raise TicketAccessDeniedError("close requires ba or admin role")

        ticket = await self._load_or_404(ticket_id)

        gate_result = await self.acceptance_gate.is_satisfied(
            ticket_id, acceptance_cycle=ticket.acceptance_cycle
        )
        if not gate_result.ok:
            raise AcceptanceGateIncompleteError(
                "Acceptance gate is not satisfied for the current cycle",
                reasons=list(gate_result.reasons),
            )

        now = self.clock()
        transition = ticket.close(actor=actor_sub, now=now)
        await self.repo.update_ticket(ticket)
        await self.repo.add_transition(transition)

        # Collect attestation IDs for the audit event (double gate visible)
        formal_dod = await self.repo.get_attestation(
            ticket_id, AttestationKind.FORMAL_DOD, ticket.acceptance_cycle
        )
        biz_value = await self.repo.get_attestation(
            ticket_id, AttestationKind.BUSINESS_VALUE, ticket.acceptance_cycle
        )

        await self.publisher(
            "ticket",
            ticket_id,
            "closed",
            before={"status": TicketStatus.ACCEPTANCE},
            after={
                "status": TicketStatus.CLOSED,
                "formal_dod_attestation_id": str(formal_dod.id) if formal_dod else None,
                "business_value_attestation_id": str(biz_value.id) if biz_value else None,
            },
        )
        return ticket, transition

    # ================================================================== #
    # Internal helpers                                                    #
    # ================================================================== #

    async def _load_or_404(self, ticket_id: UUID) -> Ticket:
        ticket = await self.repo.get_ticket_by_id(ticket_id)
        if ticket is None:
            raise TicketNotFoundError(f"Ticket {ticket_id} not found")
        return ticket

    def _assert_can_manage_ticket(
        self,
        ticket: Ticket,
        *,
        actor_id: UUID,
        roles: frozenset[str],
    ) -> None:
        """Author (per-object) or ba or admins may manage ticket metadata."""
        if ticket.author_id == actor_id or _is_ba_or_admin(roles):
            return
        raise TicketAccessDeniedError(
            "Only the ticket author, a BA, or an admin may manage this ticket"
        )

    async def _replace_assignment(
        self,
        *,
        ticket_id: UUID,
        role: AssignmentRole,
        subject: str,
        assigned_by: str,
        now: datetime,
    ) -> Assignment:
        """Close any existing active assignment for the role and create a new one."""
        existing = await self.repo.get_active_assignment(ticket_id, role)
        if existing is not None:
            await self.repo.end_assignment(existing.id, now=now)

        new_assignment = Assignment(
            id=uuid4(),
            ticket_id=ticket_id,
            role=role,
            subject=subject,
            assigned_by=assigned_by,
            assigned_at=now,
            unassigned_at=None,
        )
        await self.repo.add_assignment(new_assignment)
        return new_assignment
