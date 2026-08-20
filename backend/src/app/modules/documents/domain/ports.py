"""Port contracts for the documents module.

All ports are typing.Protocol — framework-free.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.modules.documents.domain.entities import Document


class DocumentRepository(Protocol):
    """Persistence port. Repositories flush(), never commit()."""

    async def add(self, document: Document) -> None: ...

    async def get_by_id(self, document_id: UUID) -> Document | None: ...

    async def list_documents(
        self, *, owner_id: UUID | None, limit: int, offset: int
    ) -> tuple[list[Document], int]:
        """List documents in the current tenant. owner_id=None -> the whole tenant library."""
        ...

    async def update(self, document: Document) -> Document: ...

    async def delete(self, document_id: UUID) -> None: ...

    async def get_texts(self, document_ids: tuple[UUID, ...]) -> list[tuple[UUID, str]]:
        """Extracted text for the given ids, ``status='extracted'`` only.

        Missing / not-yet-extracted / failed ids are silently omitted — callers
        detect a partial result by comparing ``len(result)`` to ``len(document_ids)``.
        """
        ...


class ObjectStorePort(Protocol):
    """Bucket-scoped object storage.

    No presigned URLs — direct multipart upload/download only. Documented
    upgrade path for large files / bandwidth offload (see the approved plan).
    """

    async def ensure_bucket(self, bucket: str) -> None: ...

    async def put(self, *, bucket: str, key: str, data: bytes, content_type: str) -> None: ...

    async def get(self, *, bucket: str, key: str) -> bytes: ...

    async def delete(self, *, bucket: str, key: str) -> None: ...

    async def delete_bucket(self, bucket: str) -> None:
        """Best-effort bucket teardown (tenant onboarding compensation only —
        S3 refuses to delete a non-empty bucket, which is fine here since
        onboarding never uploads objects before this would run)."""
        ...
