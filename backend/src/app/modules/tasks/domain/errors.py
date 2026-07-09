from __future__ import annotations

from app.core.errors import DomainError, NotFoundError


class TaskNotFoundError(NotFoundError):
    code = "TASK_NOT_FOUND"


class TaskValidationError(DomainError):
    code = "TASK_VALIDATION_ERROR"
    http_status = 422


class TaskAccessDeniedError(DomainError):
    code = "TASK_ACCESS_DENIED"
    http_status = 403


class TaskTransitionError(DomainError):
    code = "TASK_TRANSITION_FORBIDDEN"
    http_status = 409
