"""documents domain errors."""

from __future__ import annotations

from app.core.errors import DomainError, NotFoundError


class DocumentNotFoundError(NotFoundError):
    code = "DOCUMENT_NOT_FOUND"


class DocumentValidationError(DomainError):
    code = "DOCUMENT_VALIDATION_ERROR"
    http_status = 422


class DocumentAccessDeniedError(DomainError):
    code = "DOCUMENT_ACCESS_DENIED"
    http_status = 403


class UnsupportedDocumentTypeError(DomainError):
    code = "UNSUPPORTED_DOCUMENT_TYPE"
    http_status = 415


class DocumentTooLargeError(DomainError):
    code = "DOCUMENT_TOO_LARGE"
    http_status = 413


class ObjectStoreUnavailableError(DomainError):
    """The object store is not configured or the call failed. Mirrors LlmUnavailableError."""

    code = "OBJECT_STORE_UNAVAILABLE"
    http_status = 503
