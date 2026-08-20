"""DocumentService — upload, list, get, delete for the tenant document library.

Visibility model: a regular tenant member sees only their OWN uploads (these
documents can carry confidential business content); ``ba``/``tenant_admin``/
``platform_admin`` see the whole tenant library for triage support. Only the
uploader or a tenant/platform admin may delete.

Upload never fails on extraction errors — a corrupt/encrypted/unsupported file
is stored with status=failed and an extraction_error, not rejected.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from app.modules.documents.application.dtos import Page
from app.modules.documents.domain.entities import Document
from app.modules.documents.domain.errors import (
    DocumentAccessDeniedError,
    DocumentNotFoundError,
    ObjectStoreUnavailableError,
)
from app.modules.documents.domain.ports import DocumentRepository, ObjectStorePort

log = logging.getLogger(__name__)

# A flat async callable rather than a Protocol — one near-pure function
# (content_type, filename, bytes) -> text; a Protocol would be ceremony for
# a single method. Callers wrap the sync pypdf/python-docx parsers in
# asyncio.to_thread (see interface/router.py).
ExtractText = Callable[[str, str, bytes], Awaitable[str]]

_READ_ANY_ROLES = frozenset({"ba", "tenant_admin", "platform_admin"})
_MANAGE_ANY_ROLES = frozenset({"tenant_admin", "platform_admin"})


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class DocumentService:
    repo: DocumentRepository
    # None when S3_ACCESS_KEY is unset (feature disabled): upload raises
    # ObjectStoreUnavailableError (503); list/get/delete metadata still work.
    store: ObjectStorePort | None
    bucket: str
    extract_text: ExtractText
    clock: Callable[[], datetime] = field(default=_utc_now)

    async def upload(
        self, *, owner_id: UUID, filename: str, content_type: str, data: bytes
    ) -> Document:
        if self.store is None:
            raise ObjectStoreUnavailableError(
                "Document storage is not configured (S3_ACCESS_KEY is not set)"
            )
        # Validate (cheap, no I/O) before touching the object store — a bad
        # content-type/size fails fast without ever writing an orphan object.
        document = Document.create(
            owner_id=owner_id,
            filename=filename,
            content_type=content_type,
            size_bytes=len(data),
            now=self.clock(),
        )
        await self.store.ensure_bucket(self.bucket)
        # Object write BEFORE the row insert: a row pointing at a missing
        # object would be visible to the user; an orphaned object with no row
        # is safe (unreachable), just a storage-cost footgun if it recurs.
        await self.store.put(
            bucket=self.bucket, key=document.storage_key, data=data, content_type=content_type
        )
        await self.repo.add(document)

        try:
            text = await self.extract_text(content_type, document.filename, data)
            document.mark_extracted(text, now=self.clock())
        except Exception as exc:
            log.warning("document.extraction_failed id=%s error=%s", document.id, exc)
            document.mark_failed(str(exc), now=self.clock())
        await self.repo.update(document)
        return document

    async def get(self, *, document_id: UUID, actor_id: UUID, roles: frozenset[str]) -> Document:
        return await self._load_owned(document_id, actor_id, roles, _READ_ANY_ROLES)

    async def list(
        self, *, actor_id: UUID, roles: frozenset[str], mine: bool, limit: int, offset: int
    ) -> Page:
        """Regular members always see their own uploads; ba/admin see the
        whole tenant library unless they explicitly ask for `mine`."""
        owner_filter = None if (not mine and (roles & _READ_ANY_ROLES)) else actor_id
        items, total = await self.repo.list_documents(
            owner_id=owner_filter, limit=limit, offset=offset
        )
        return Page(items=items, total=total, limit=limit, offset=offset)

    async def delete(self, *, document_id: UUID, actor_id: UUID, roles: frozenset[str]) -> None:
        document = await self._load_owned(document_id, actor_id, roles, _MANAGE_ANY_ROLES)
        await self.repo.delete(document.id)
        if self.store is None:
            return
        try:
            await self.store.delete(bucket=self.bucket, key=document.storage_key)
        except Exception as exc:
            log.warning("document.store_delete_failed id=%s error=%s", document.id, exc)

    async def _load_owned(
        self,
        document_id: UUID,
        actor_id: UUID,
        roles: frozenset[str],
        allowed_extra_roles: frozenset[str],
    ) -> Document:
        document = await self.repo.get_by_id(document_id)
        if document is None:
            raise DocumentNotFoundError(f"Document {document_id} not found")
        if document.owner_id != actor_id and not (roles & allowed_extra_roles):
            raise DocumentAccessDeniedError(
                "Only the uploader or an authorized role can access this document"
            )
        return document
