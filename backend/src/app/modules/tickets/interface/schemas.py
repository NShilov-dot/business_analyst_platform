"""Pydantic request/response schemas for /api/v1/tickets.

Requests convert to application-layer commands via ``to_command()``;
responses are built from domain entities via ``from_entity()``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.intake_templates.domain.entities import FREE_FORM_VERSION_ID
from app.modules.tickets.application.dtos import (
    AssignCommand,
    AttestBusinessValueCommand,
    AttestFormalDodCommand,
    AttestSpecApprovalCommand,
    CreateTicketCommand,
    RejectCommand,
    ReplaceSubmissionCommand,
    RequestAcceptanceCommand,
    ReturnForRefinementCommand,
    ReturnToWorkCommand,
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
    TicketPriority,
    TicketStatus,
    TriageDecision,
    TriageOutcome,
)

T = TypeVar("T")

# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------


class CreateTicketRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4_000)
    priority: TicketPriority | None = None
    template_version_id: UUID = FREE_FORM_VERSION_ID
    payload: dict[str, object] = Field(default_factory=dict)

    def to_command(self) -> CreateTicketCommand:
        return CreateTicketCommand(
            title=self.title,
            description=self.description,
            priority=self.priority,
            template_version_id=self.template_version_id,
            payload=self.payload,
        )


class UpdateTicketRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4_000)
    priority: TicketPriority | None = None

    def to_command(self) -> UpdateTicketCommand:
        provided = self.model_fields_set
        return UpdateTicketCommand(
            title=self.title,
            description=self.description,
            description_set="description" in provided,
            priority=self.priority,
            priority_set="priority" in provided,
        )


class ReplaceSubmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_version_id: UUID
    payload: dict[str, object]

    def to_command(self) -> ReplaceSubmissionCommand:
        return ReplaceSubmissionCommand(
            template_version_id=self.template_version_id,
            payload=self.payload,
        )


class TriageAcceptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    department_id: UUID
    business_owner_subject: str = Field(min_length=1, max_length=255)
    executor_subject: str | None = Field(default=None, max_length=255)
    priority: TicketPriority | None = None
    comment: str | None = Field(default=None, max_length=4_000)

    def to_command(self) -> TriageAcceptCommand:
        return TriageAcceptCommand(
            department_id=self.department_id,
            business_owner_subject=self.business_owner_subject,
            executor_subject=self.executor_subject,
            priority=self.priority,
            comment=self.comment,
        )


class CommentRequest(BaseModel):
    """Shared body for edges where a comment is mandatory."""

    model_config = ConfigDict(extra="forbid")

    comment: str = Field(min_length=1, max_length=4_000)

    def to_return_for_refinement(self) -> ReturnForRefinementCommand:
        return ReturnForRefinementCommand(comment=self.comment)

    def to_return_to_work(self) -> ReturnToWorkCommand:
        return ReturnToWorkCommand(comment=self.comment)


class RejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comment: str = Field(min_length=1, max_length=4_000)
    rejection_reason: RejectionReason | None = None
    duplicate_of_ticket_id: UUID | None = None

    def to_command(self) -> RejectCommand:
        return RejectCommand(
            comment=self.comment,
            rejection_reason=self.rejection_reason,
            duplicate_of_ticket_id=self.duplicate_of_ticket_id,
        )


class AssignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: AssignmentRole
    subject: str = Field(min_length=1, max_length=255)

    def to_command(self) -> AssignCommand:
        return AssignCommand(role=self.role, subject=self.subject)


class AttestSpecApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Optional: the ticket itself is the spec, so the server self-references it when
    # omitted. An explicit external ref (Confluence/tracker URL) is still accepted.
    spec_ref: str | None = Field(default=None, min_length=1, max_length=500)
    agreed_with_subject: str = Field(min_length=1, max_length=255)
    comment: str | None = Field(default=None, max_length=4_000)

    def to_command(self) -> AttestSpecApprovalCommand:
        return AttestSpecApprovalCommand(
            spec_ref=self.spec_ref,
            agreed_with_subject=self.agreed_with_subject,
            comment=self.comment,
        )


class AttestFormalDodRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checklist: dict[str, bool] = Field(min_length=1)
    comment: str | None = Field(default=None, max_length=4_000)

    def to_command(self) -> AttestFormalDodCommand:
        return AttestFormalDodCommand(checklist=self.checklist, comment=self.comment)


class AttestBusinessValueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comment: str | None = Field(default=None, max_length=4_000)

    def to_command(self) -> AttestBusinessValueCommand:
        return AttestBusinessValueCommand(comment=self.comment)


class RequestAcceptanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    changes_summary: str = Field(min_length=1, max_length=4_000)

    def to_command(self) -> RequestAcceptanceCommand:
        return RequestAcceptanceCommand(changes_summary=self.changes_summary)


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


class TicketResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    description: str | None
    status: TicketStatus
    author_id: UUID
    department_id: UUID | None
    priority: TicketPriority | None
    acceptance_cycle: int
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None

    @classmethod
    def from_entity(cls, ticket: Ticket) -> TicketResponse:
        return cls.model_validate(ticket)


class TransitionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    ticket_id: UUID
    from_status: TicketStatus | None
    to_status: TicketStatus
    actor: str
    comment: str | None
    acceptance_cycle: int
    occurred_at: datetime

    @classmethod
    def from_entity(cls, transition: StatusTransition) -> TransitionResponse:
        return cls.model_validate(transition)


class SubmissionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    ticket_id: UUID
    version: int
    template_version_id: UUID
    payload: dict[str, object]
    created_by: str
    created_at: datetime

    @classmethod
    def from_entity(cls, submission: IntakeSubmission) -> SubmissionResponse:
        return cls.model_validate(submission)


class TriageDecisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    ticket_id: UUID
    outcome: TriageOutcome
    department_id: UUID | None
    rejection_reason: RejectionReason | None
    duplicate_of_ticket_id: UUID | None
    comment: str | None
    decided_by: str
    decided_at: datetime

    @classmethod
    def from_entity(cls, decision: TriageDecision) -> TriageDecisionResponse:
        return cls.model_validate(decision)


class AssignmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    ticket_id: UUID
    role: AssignmentRole
    subject: str
    assigned_by: str
    assigned_at: datetime
    unassigned_at: datetime | None

    @classmethod
    def from_entity(cls, assignment: Assignment) -> AssignmentResponse:
        return cls.model_validate(assignment)


class AttestationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    ticket_id: UUID
    kind: AttestationKind
    acceptance_cycle: int
    attested_by: str
    roles_snapshot: list[str]
    checklist: dict[str, bool] | None
    spec_ref: str | None
    agreed_with_subject: str | None
    comment: str | None
    attested_at: datetime

    @classmethod
    def from_entity(cls, attestation: GateAttestation) -> AttestationResponse:
        return cls.model_validate(attestation)


class TicketDetailResponse(BaseModel):
    ticket: TicketResponse
    current_submission: SubmissionResponse | None
    assignments: list[AssignmentResponse]
    attestations: list[AttestationResponse]
    triage_decisions: list[TriageDecisionResponse]

    @classmethod
    def from_detail(cls, detail: TicketDetail) -> TicketDetailResponse:
        return cls(
            ticket=TicketResponse.from_entity(detail.ticket),
            current_submission=(
                SubmissionResponse.from_entity(detail.current_submission)
                if detail.current_submission is not None
                else None
            ),
            assignments=[AssignmentResponse.from_entity(a) for a in detail.assignments],
            attestations=[AttestationResponse.from_entity(a) for a in detail.attestations],
            triage_decisions=[
                TriageDecisionResponse.from_entity(d) for d in detail.triage_decisions
            ],
        )


# ---------------------------------------------------------------------------
# Envelopes
# ---------------------------------------------------------------------------


class PageMeta(BaseModel):
    total: int
    limit: int
    offset: int


class Envelope(BaseModel, Generic[T]):
    data: T


class PagedEnvelope(BaseModel, Generic[T]):
    data: list[T]
    meta: PageMeta

    @classmethod
    def from_page(cls, page: TicketPage) -> PagedEnvelope[TicketResponse]:
        return PagedEnvelope[TicketResponse](
            data=[TicketResponse.from_entity(t) for t in page.items],
            meta=PageMeta(total=page.total, limit=page.limit, offset=page.offset),
        )
