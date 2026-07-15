"""Department application errors. No framework imports allowed."""

from __future__ import annotations

from app.core.errors import ConflictError, NotFoundError


class DepartmentNotFoundError(NotFoundError):
    code = "DEPARTMENT_NOT_FOUND"


class DepartmentNameConflictError(ConflictError):
    code = "DEPARTMENT_NAME_CONFLICT"


class MembershipNotFoundError(NotFoundError):
    code = "MEMBERSHIP_NOT_FOUND"
