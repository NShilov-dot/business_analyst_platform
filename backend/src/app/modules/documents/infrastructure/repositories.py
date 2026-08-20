from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.documents.domain.entities import Document, DocumentStatus
from app.modules.documents.domain.errors import DocumentNotFoundError
from app.modules.documents.infrastructure.models import DocumentRow


def _to_entity(row: DocumentRow) -> Document:
    return Document(
        id=row.id,
        owner_id=row.owner_id,
        filename=row.filename,
        content_type=row.content_type,
        size_bytes=row.size_bytes,
        storage_key=row.storage_key,
        status=DocumentStatus(row.status),
        extracted_text=row.extracted_text,
        extraction_error=row.extraction_error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _apply_to_row(document: Document, row: DocumentRow) -> None:
    row.status = document.status.value
    row.extracted_text = document.extracted_text
    row.extraction_error = document.extraction_error
    row.updated_at = document.updated_at


class SqlAlchemyDocumentRepository:
    """Concrete DocumentRepository on AsyncSession. Caller owns the transaction —
    the session-per-request dependency commits/rolls back."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, document: Document) -> None:
        row = DocumentRow(
            id=document.id,
            owner_id=document.owner_id,
            filename=document.filename,
            content_type=document.content_type,
            size_bytes=document.size_bytes,
            storage_key=document.storage_key,
            status=document.status.value,
            extracted_text=document.extracted_text,
            extraction_error=document.extraction_error,
            created_at=document.created_at,
            updated_at=document.updated_at,
        )
        self._session.add(row)
        await self._session.flush()

    async def get_by_id(self, document_id: UUID) -> Document | None:
        row = await self._session.get(DocumentRow, document_id)
        return _to_entity(row) if row is not None else None

    async def list_documents(
        self, *, owner_id: UUID | None, limit: int, offset: int
    ) -> tuple[list[Document], int]:
        base = select(DocumentRow)
        count_q = select(func.count()).select_from(DocumentRow)
        if owner_id is not None:
            base = base.where(DocumentRow.owner_id == owner_id)
            count_q = count_q.where(DocumentRow.owner_id == owner_id)

        rows = (
            await self._session.scalars(
                base.order_by(DocumentRow.created_at.desc()).limit(limit).offset(offset)
            )
        ).all()
        total = await self._session.scalar(count_q) or 0
        return [_to_entity(r) for r in rows], total

    async def update(self, document: Document) -> Document:
        row = await self._session.get(DocumentRow, document.id)
        if row is None:
            raise DocumentNotFoundError(f"Document {document.id} not found")
        _apply_to_row(document, row)
        await self._session.flush()
        return document

    async def delete(self, document_id: UUID) -> None:
        await self._session.execute(delete(DocumentRow).where(DocumentRow.id == document_id))
        await self._session.flush()

    async def get_texts(self, document_ids: tuple[UUID, ...]) -> list[tuple[UUID, str]]:
        if not document_ids:
            return []
        rows = (
            await self._session.scalars(
                select(DocumentRow).where(
                    DocumentRow.id.in_(document_ids),
                    DocumentRow.status == DocumentStatus.EXTRACTED.value,
                )
            )
        ).all()
        return [(r.id, r.extracted_text or "") for r in rows]
