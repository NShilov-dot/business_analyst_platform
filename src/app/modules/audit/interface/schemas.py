"""Pydantic response schemas for the audit API.

Read-only: no request schemas are needed since audit is INSERT-only from the
subscriber. The `Envelope[T]` and `PagedEnvelope[T]` pattern follows
`departments/interface/schemas.py`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.modules.audit.application.dtos import AuditEntriesPage, AuditEntry

T = TypeVar("T")


class AuditEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    entity_type: str
    entity_id: UUID
    action: str
    actor: str
    roles: list[str]
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    request_id: str | None
    occurred_at: datetime

    @classmethod
    def from_entry(cls, entry: AuditEntry) -> AuditEntryResponse:
        return cls(
            id=entry.id,
            entity_type=entry.entity_type,
            entity_id=entry.entity_id,
            action=entry.action,
            actor=entry.actor,
            roles=entry.roles,
            before=entry.before,
            after=entry.after,
            request_id=entry.request_id,
            occurred_at=entry.occurred_at,
        )


# ---------------------------------------------------------------------------
# Envelope wrappers (local copies — avoids cross-module imports)
# ---------------------------------------------------------------------------


class PageMeta(BaseModel):
    total: int
    limit: int
    offset: int


class PagedEnvelope(BaseModel, Generic[T]):
    data: list[T]
    meta: PageMeta

    @classmethod
    def from_page(cls, page: AuditEntriesPage) -> PagedEnvelope[AuditEntryResponse]:
        return PagedEnvelope[AuditEntryResponse](
            data=[AuditEntryResponse.from_entry(e) for e in page.items],
            meta=PageMeta(total=page.total, limit=page.limit, offset=page.offset),
        )
