"""Concrete TicketRepository on AsyncSession.

Caller owns the transaction — repositories flush(), never commit().
"""

from __future__ import annotations

from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.tickets.domain.entities import (
    Assignment,
    AssignmentRole,
    AttestationKind,
    GateAttestation,
    IntakeSubmission,
    RejectionReason,
    StatusTransition,
    Ticket,
    TicketPriority,
    TicketStatus,
    TriageDecision,
    TriageOutcome,
)
from app.modules.tickets.domain.errors import AttestationDuplicateError
from app.modules.tickets.infrastructure.models import (
    AssignmentRow,
    GateAttestationRow,
    IntakeSubmissionRow,
    TicketRow,
    TicketStatusTransitionRow,
    TriageDecisionRow,
)

# Postgres SQLSTATE for a unique-constraint violation (vs FK/CHECK/NOT-NULL).
_PG_UNIQUE_VIOLATION = "23505"


# ---------------------------------------------------------------------------
# Row <-> entity mapping
# ---------------------------------------------------------------------------


def _ticket_to_entity(row: TicketRow) -> Ticket:
    return Ticket(
        id=row.id,
        title=row.title,
        description=row.description,
        status=TicketStatus(row.status),
        author_id=row.author_id,
        department_id=row.department_id,
        priority=TicketPriority(row.priority) if row.priority is not None else None,
        acceptance_cycle=row.acceptance_cycle,
        created_at=row.created_at,
        updated_at=row.updated_at,
        closed_at=row.closed_at,
    )


def _transition_to_entity(row: TicketStatusTransitionRow) -> StatusTransition:
    return StatusTransition(
        id=row.id,
        ticket_id=row.ticket_id,
        from_status=TicketStatus(row.from_status) if row.from_status is not None else None,
        to_status=TicketStatus(row.to_status),
        actor=row.actor,
        comment=row.comment,
        acceptance_cycle=row.acceptance_cycle,
        occurred_at=row.occurred_at,
    )


def _submission_to_entity(row: IntakeSubmissionRow) -> IntakeSubmission:
    return IntakeSubmission(
        id=row.id,
        ticket_id=row.ticket_id,
        version=row.version,
        template_version_id=row.template_version_id,
        payload=cast(dict[str, object], row.payload),
        created_by=row.created_by,
        created_at=row.created_at,
    )


def _triage_to_entity(row: TriageDecisionRow) -> TriageDecision:
    return TriageDecision(
        id=row.id,
        ticket_id=row.ticket_id,
        outcome=TriageOutcome(row.outcome),
        department_id=row.department_id,
        rejection_reason=(
            RejectionReason(row.rejection_reason) if row.rejection_reason is not None else None
        ),
        duplicate_of_ticket_id=row.duplicate_of_ticket_id,
        comment=row.comment,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
    )


def _assignment_to_entity(row: AssignmentRow) -> Assignment:
    return Assignment(
        id=row.id,
        ticket_id=row.ticket_id,
        role=AssignmentRole(row.role),
        subject=row.subject,
        assigned_by=row.assigned_by,
        assigned_at=row.assigned_at,
        unassigned_at=row.unassigned_at,
    )


def _attestation_to_entity(row: GateAttestationRow) -> GateAttestation:
    return GateAttestation(
        id=row.id,
        ticket_id=row.ticket_id,
        kind=AttestationKind(row.kind),
        acceptance_cycle=row.acceptance_cycle,
        attested_by=row.attested_by,
        roles_snapshot=list(row.roles_snapshot),
        checklist=cast(dict[str, bool] | None, row.checklist),
        spec_ref=row.spec_ref,
        agreed_with_subject=row.agreed_with_subject,
        comment=row.comment,
        attested_at=row.attested_at,
    )


class SqlAlchemyTicketRepository:
    """Concrete TicketRepository. Tenant isolation comes from the per-request
    ``search_path`` — every query here is already scoped to one tenant schema."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- Ticket CRUD -------------------------------------------------------

    async def add_ticket(self, ticket: Ticket) -> None:
        row = TicketRow(
            id=ticket.id,
            title=ticket.title,
            description=ticket.description,
            status=ticket.status.value,
            author_id=ticket.author_id,
            department_id=ticket.department_id,
            priority=ticket.priority.value if ticket.priority is not None else None,
            acceptance_cycle=ticket.acceptance_cycle,
            created_at=ticket.created_at,
            updated_at=ticket.updated_at,
            closed_at=ticket.closed_at,
        )
        self._session.add(row)
        await self._session.flush()

    async def get_ticket_by_id(self, ticket_id: UUID) -> Ticket | None:
        row = await self._session.get(TicketRow, ticket_id)
        return _ticket_to_entity(row) if row is not None else None

    async def update_ticket(self, ticket: Ticket) -> None:
        row = await self._session.get(TicketRow, ticket.id)
        if row is None:  # pragma: no cover — service loads before updating
            return
        row.title = ticket.title
        row.description = ticket.description
        row.status = ticket.status.value
        row.department_id = ticket.department_id
        row.priority = ticket.priority.value if ticket.priority is not None else None
        row.acceptance_cycle = ticket.acceptance_cycle
        row.updated_at = ticket.updated_at
        row.closed_at = ticket.closed_at
        await self._session.flush()

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
        base = select(TicketRow)
        count_q = select(func.count()).select_from(TicketRow)

        if status is not None:
            base = base.where(TicketRow.status == status.value)
            count_q = count_q.where(TicketRow.status == status.value)
        if department_id is not None:
            base = base.where(TicketRow.department_id == department_id)
            count_q = count_q.where(TicketRow.department_id == department_id)
        if author_id is not None:
            base = base.where(TicketRow.author_id == author_id)
            count_q = count_q.where(TicketRow.author_id == author_id)
        if assigned_to is not None:
            active = (
                select(AssignmentRow.ticket_id)
                .where(
                    AssignmentRow.subject == assigned_to,
                    AssignmentRow.unassigned_at.is_(None),
                )
                .scalar_subquery()
            )
            base = base.where(TicketRow.id.in_(active))
            count_q = count_q.where(TicketRow.id.in_(active))

        rows = (
            await self._session.scalars(
                base.order_by(TicketRow.created_at.desc()).limit(limit).offset(offset)
            )
        ).all()
        total = await self._session.scalar(count_q) or 0
        return [_ticket_to_entity(r) for r in rows], total

    # -- StatusTransition --------------------------------------------------

    async def add_transition(self, transition: StatusTransition) -> None:
        row = TicketStatusTransitionRow(
            id=transition.id,
            ticket_id=transition.ticket_id,
            from_status=(
                transition.from_status.value if transition.from_status is not None else None
            ),
            to_status=transition.to_status.value,
            actor=transition.actor,
            comment=transition.comment,
            acceptance_cycle=transition.acceptance_cycle,
            occurred_at=transition.occurred_at,
        )
        self._session.add(row)
        await self._session.flush()

    async def list_transitions(self, ticket_id: UUID) -> list[StatusTransition]:
        rows = (
            await self._session.scalars(
                select(TicketStatusTransitionRow)
                .where(TicketStatusTransitionRow.ticket_id == ticket_id)
                .order_by(TicketStatusTransitionRow.occurred_at)
            )
        ).all()
        return [_transition_to_entity(r) for r in rows]

    # -- IntakeSubmission --------------------------------------------------

    async def add_submission(self, submission: IntakeSubmission) -> None:
        row = IntakeSubmissionRow(
            id=submission.id,
            ticket_id=submission.ticket_id,
            version=submission.version,
            template_version_id=submission.template_version_id,
            payload=submission.payload,
            created_by=submission.created_by,
            created_at=submission.created_at,
        )
        self._session.add(row)
        await self._session.flush()

    async def get_current_submission(self, ticket_id: UUID) -> IntakeSubmission | None:
        row = await self._session.scalar(
            select(IntakeSubmissionRow)
            .where(IntakeSubmissionRow.ticket_id == ticket_id)
            .order_by(IntakeSubmissionRow.version.desc())
            .limit(1)
        )
        return _submission_to_entity(row) if row is not None else None

    async def list_submissions(self, ticket_id: UUID) -> list[IntakeSubmission]:
        rows = (
            await self._session.scalars(
                select(IntakeSubmissionRow)
                .where(IntakeSubmissionRow.ticket_id == ticket_id)
                .order_by(IntakeSubmissionRow.version)
            )
        ).all()
        return [_submission_to_entity(r) for r in rows]

    # -- TriageDecision ----------------------------------------------------

    async def add_triage_decision(self, decision: TriageDecision) -> None:
        row = TriageDecisionRow(
            id=decision.id,
            ticket_id=decision.ticket_id,
            outcome=decision.outcome.value,
            department_id=decision.department_id,
            rejection_reason=(
                decision.rejection_reason.value
                if decision.rejection_reason is not None
                else None
            ),
            duplicate_of_ticket_id=decision.duplicate_of_ticket_id,
            comment=decision.comment,
            decided_by=decision.decided_by,
            decided_at=decision.decided_at,
        )
        self._session.add(row)
        await self._session.flush()

    async def list_triage_decisions(self, ticket_id: UUID) -> list[TriageDecision]:
        rows = (
            await self._session.scalars(
                select(TriageDecisionRow)
                .where(TriageDecisionRow.ticket_id == ticket_id)
                .order_by(TriageDecisionRow.decided_at)
            )
        ).all()
        return [_triage_to_entity(r) for r in rows]

    # -- Assignment --------------------------------------------------------

    async def add_assignment(self, assignment: Assignment) -> None:
        row = AssignmentRow(
            id=assignment.id,
            ticket_id=assignment.ticket_id,
            role=assignment.role.value,
            subject=assignment.subject,
            assigned_by=assignment.assigned_by,
            assigned_at=assignment.assigned_at,
            unassigned_at=assignment.unassigned_at,
        )
        self._session.add(row)
        await self._session.flush()

    async def get_active_assignment(
        self, ticket_id: UUID, role: AssignmentRole
    ) -> Assignment | None:
        row = await self._session.scalar(
            select(AssignmentRow).where(
                AssignmentRow.ticket_id == ticket_id,
                AssignmentRow.role == role.value,
                AssignmentRow.unassigned_at.is_(None),
            )
        )
        return _assignment_to_entity(row) if row is not None else None

    async def end_assignment(self, assignment_id: UUID, *, now: datetime) -> None:
        await self._session.execute(
            update(AssignmentRow)
            .where(AssignmentRow.id == assignment_id)
            .values(unassigned_at=now)
        )
        await self._session.flush()

    # -- GateAttestation ---------------------------------------------------

    async def add_attestation(self, attestation: GateAttestation) -> None:
        row = GateAttestationRow(
            id=attestation.id,
            ticket_id=attestation.ticket_id,
            kind=attestation.kind.value,
            acceptance_cycle=attestation.acceptance_cycle,
            attested_by=attestation.attested_by,
            roles_snapshot=attestation.roles_snapshot,
            checklist=cast(dict[str, object] | None, attestation.checklist),
            spec_ref=attestation.spec_ref,
            agreed_with_subject=attestation.agreed_with_subject,
            comment=attestation.comment,
            attested_at=attestation.attested_at,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            # ONLY a unique violation (sqlstate 23505) means a racing request
            # inserted the same (ticket_id, kind, acceptance_cycle) between the
            # service's duplicate check and this flush → the documented 409.
            # FK / CHECK / NOT-NULL violations are real bugs; re-raise them so
            # they don't masquerade as a duplicate.
            if getattr(exc.orig, "sqlstate", None) != _PG_UNIQUE_VIOLATION:
                raise
            raise AttestationDuplicateError(
                f"A {attestation.kind.value} attestation for cycle "
                f"{attestation.acceptance_cycle} already exists"
            ) from exc

    async def get_attestation(
        self,
        ticket_id: UUID,
        kind: str,
        cycle: int,
    ) -> GateAttestation | None:
        row = await self._session.scalar(
            select(GateAttestationRow).where(
                GateAttestationRow.ticket_id == ticket_id,
                GateAttestationRow.kind == str(kind),
                GateAttestationRow.acceptance_cycle == cycle,
            )
        )
        return _attestation_to_entity(row) if row is not None else None

    async def list_attestations(self, ticket_id: UUID) -> list[GateAttestation]:
        rows = (
            await self._session.scalars(
                select(GateAttestationRow)
                .where(GateAttestationRow.ticket_id == ticket_id)
                .order_by(GateAttestationRow.attested_at)
            )
        ).all()
        return [_attestation_to_entity(r) for r in rows]
