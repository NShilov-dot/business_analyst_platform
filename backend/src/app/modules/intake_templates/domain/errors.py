"""Intake-template domain errors. No framework imports allowed."""

from __future__ import annotations

from typing import Any

from app.core.errors import ConflictError, DomainError, NotFoundError


class TemplateNotFoundError(NotFoundError):
    code = "TEMPLATE_NOT_FOUND"


class TemplateVersionNotFoundError(NotFoundError):
    code = "TEMPLATE_VERSION_NOT_FOUND"


class VersionNotDraftError(DomainError):
    """The version must be in draft status for the requested operation (e.g. publish)."""

    code = "VERSION_INVALID_STATUS"
    http_status = 409


class VersionNotPublishedError(DomainError):
    """The version must be in published status for the requested operation (e.g. archive)."""

    code = "VERSION_NOT_PUBLISHED"
    http_status = 409


class PublishedVersionImmutableError(DomainError):
    """Published versions are immutable; edits must go through a new draft."""

    code = "PUBLISHED_VERSION_IMMUTABLE"
    http_status = 409


class MandatoryCoreMissingError(DomainError):
    """A publish attempt is missing required mandatory-core field keys."""

    code = "MANDATORY_CORE_MISSING"
    http_status = 422

    def __init__(self, message: str, *, missing_keys: list[str]) -> None:
        details: list[dict[str, Any]] = [{"missing_keys": missing_keys}]
        super().__init__(message, details=details)
        self.missing_keys = missing_keys


class DuplicateTemplateNameError(ConflictError):
    code = "DUPLICATE_TEMPLATE_NAME"


class SystemTemplateProtectedError(DomainError):
    """Operation is not allowed on a system (built-in) template."""

    code = "SYSTEM_TEMPLATE_PROTECTED"
    http_status = 409
