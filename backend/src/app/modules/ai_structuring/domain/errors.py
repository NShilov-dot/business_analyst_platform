"""ai_structuring domain errors."""

from __future__ import annotations

from app.core.errors import DomainError, NotFoundError, PermissionDeniedError


class ChatSessionNotFoundError(NotFoundError):
    code = "CHAT_SESSION_NOT_FOUND"


class ChatAccessDeniedError(PermissionDeniedError):
    """Only the session's requester may read or continue their chat."""

    code = "CHAT_ACCESS_DENIED"


class ChatSessionClosedError(DomainError):
    """The session is submitted or discarded — no further mutations."""

    code = "CHAT_SESSION_CLOSED"
    http_status = 409


class ChatValidationError(DomainError):
    code = "CHAT_VALIDATION_ERROR"
    http_status = 422


class DraftIncompleteError(DomainError):
    """Finalize was requested while the draft still fails template validation.

    ``details`` carries the per-field errors from SubmissionValidator.
    """

    code = "DRAFT_INCOMPLETE"
    http_status = 422

    def __init__(
        self, message: str, *, field_errors: list[dict[str, str]] | None = None
    ) -> None:
        super().__init__(message, details=field_errors or [])


class LlmUnavailableError(DomainError):
    """The LLM provider is not configured or the call failed."""

    code = "LLM_UNAVAILABLE"
    http_status = 503
