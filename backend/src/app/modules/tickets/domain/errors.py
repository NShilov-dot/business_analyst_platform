"""Tickets module domain errors.

Every error maps to an exact HTTP status and carries a machine-readable code
so the frontend and integration tests can rely on stable contracts.

Error codes are verbatim from the spec (tickets-phase1.md §6).
"""

from __future__ import annotations

from typing import Any

from app.core.errors import DomainError, NotFoundError, PermissionDeniedError


class TicketNotFoundError(NotFoundError):
    """Ticket does not exist in the tenant schema."""

    code = "TICKET_NOT_FOUND"


class TicketValidationError(DomainError):
    """Payload failed structural validation (declared keys, title length, etc.)."""

    code = "TICKET_VALIDATION_ERROR"
    http_status = 422


class SubmissionInvalidError(DomainError):
    """Submission payload failed SubmissionValidator field-level checks.

    ``details`` is a list of ``{"key": ..., "message": ...}`` dicts — one per
    FieldError returned by ``SubmissionValidator.validate_submission``.
    """

    code = "SUBMISSION_INVALID"
    http_status = 422

    def __init__(
        self,
        message: str,
        *,
        field_errors: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message, details=field_errors or [])


class TicketSubmissionFrozenError(DomainError):
    """New intake submission versions are only allowed while status == created."""

    code = "TICKET_SUBMISSION_FROZEN"
    http_status = 409


class TicketTransitionForbiddenError(DomainError):
    """The requested (from_status, to_status) edge is not in ALLOWED_TRANSITIONS."""

    code = "TICKET_TRANSITION_FORBIDDEN"
    http_status = 409


class TicketAccessDeniedError(PermissionDeniedError):
    """The caller lacks the required role or per-object right for this action."""

    code = "TICKET_ACCESS_DENIED"


class AssignmentMissingError(DomainError):
    """A required assignment (business_owner or executor) is absent."""

    code = "ASSIGNMENT_MISSING"
    http_status = 409


class SpecNotApprovedError(DomainError):
    """SpecApprovalGate.is_satisfied() returned ok=False.

    ``details`` carries the gate's ``reasons`` list.
    """

    code = "SPEC_NOT_APPROVED"
    http_status = 409

    def __init__(self, message: str, *, reasons: list[str] | None = None) -> None:
        super().__init__(message, details=[{"reason": r} for r in (reasons or [])])


class AcceptanceGateIncompleteError(DomainError):
    """AcceptanceGate.is_satisfied() returned ok=False.

    ``details`` carries the gate's ``reasons`` list.
    """

    code = "ACCEPTANCE_GATE_INCOMPLETE"
    http_status = 409

    def __init__(self, message: str, *, reasons: list[str] | None = None) -> None:
        super().__init__(message, details=[{"reason": r} for r in (reasons or [])])


class AttestationNotAllowedError(PermissionDeniedError):
    """The caller is not permitted to sign this attestation kind."""

    code = "ATTESTATION_NOT_ALLOWED"


class AttestationDuplicateError(DomainError):
    """A GateAttestation (ticket, kind, cycle) already exists."""

    code = "ATTESTATION_DUPLICATE"
    http_status = 409


class TriageDecisionInvalidError(DomainError):
    """The triage decision payload violated a domain invariant."""

    code = "TRIAGE_DECISION_INVALID"
    http_status = 422
