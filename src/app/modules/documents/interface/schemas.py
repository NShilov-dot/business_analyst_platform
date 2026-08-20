"""Pydantic schemas for /v1/documents."""

from __future__ import annotations

from datetime import datetime
from typing import Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.modules.documents.application.dtos import Page
from app.modules.documents.domain.entities import Document, DocumentStatus

T = TypeVar("T")


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    filename: str
    content_type: str
    size_bytes: int
    status: DocumentStatus
    extraction_error: str | None
    created_at: datetime

    @classmethod
    def from_entity(cls, document: Document) -> DocumentResponse:
        return cls.model_validate(document)


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
    def from_page(cls, page: Page) -> PagedEnvelope[DocumentResponse]:
        return PagedEnvelope[DocumentResponse](
            data=[DocumentResponse.from_entity(d) for d in page.items],
            meta=PageMeta(total=page.total, limit=page.limit, offset=page.offset),
        )
