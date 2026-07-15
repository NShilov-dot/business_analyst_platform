"""Audit module errors. No framework imports allowed."""

from __future__ import annotations

from app.core.errors import NotFoundError


class AuditEntryNotFoundError(NotFoundError):
    code = "AUDIT_ENTRY_NOT_FOUND"
