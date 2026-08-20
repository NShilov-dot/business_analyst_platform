"""DocumentService tests with fake ObjectStorePort + repo (no live MinIO)."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.modules.documents.application.services import DocumentService
from app.modules.documents.domain.entities import Document, DocumentStatus
from app.modules.documents.domain.errors import (
    DocumentAccessDeniedError,
    DocumentNotFoundError,
    ObjectStoreUnavailableError,
)

_TS = datetime(2026, 8, 18, 10, 0, tzinfo=UTC)
_OWNER_ID = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
_OTHER_ID = UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")
_TXT_CONTENT_TYPE = "text/plain"


class FakeObjectStore:
    def __init__(self) -> None:
        self.buckets: set[str] = set()
        self.puts: list[tuple[str, str, bytes, str]] = []
        self.deleted: list[tuple[str, str]] = []

    async def ensure_bucket(self, bucket: str) -> None:
        self.buckets.add(bucket)

    async def put(self, *, bucket: str, key: str, data: bytes, content_type: str) -> None:
        self.puts.append((bucket, key, data, content_type))

    async def get(self, *, bucket: str, key: str) -> bytes:
        for b, k, data, _ in self.puts:
            if b == bucket and k == key:
                return data
        raise KeyError(key)

    async def delete(self, *, bucket: str, key: str) -> None:
        self.deleted.append((bucket, key))

    async def delete_bucket(self, bucket: str) -> None:
        self.buckets.discard(bucket)


class FakeDocumentRepository:
    def __init__(self) -> None:
        self.rows: dict[UUID, Document] = {}

    async def add(self, document: Document) -> None:
        self.rows[document.id] = dataclasses.replace(document)

    async def get_by_id(self, document_id: UUID) -> Document | None:
        row = self.rows.get(document_id)
        return dataclasses.replace(row) if row is not None else None

    async def list_documents(
        self, *, owner_id: UUID | None, limit: int, offset: int
    ) -> tuple[list[Document], int]:
        items = [d for d in self.rows.values() if owner_id is None or d.owner_id == owner_id]
        items.sort(key=lambda d: d.created_at, reverse=True)
        return items[offset : offset + limit], len(items)

    async def update(self, document: Document) -> Document:
        self.rows[document.id] = dataclasses.replace(document)
        return document

    async def delete(self, document_id: UUID) -> None:
        self.rows.pop(document_id, None)

    async def get_texts(self, document_ids: tuple[UUID, ...]) -> list[tuple[UUID, str]]:
        return [
            (d.id, d.extracted_text or "")
            for d in self.rows.values()
            if d.id in document_ids and d.status == DocumentStatus.EXTRACTED
        ]


async def _succeeding_extractor(content_type: str, filename: str, data: bytes) -> str:
    return data.decode("utf-8")


async def _failing_extractor(content_type: str, filename: str, data: bytes) -> str:
    raise ValueError("corrupt file")


def _service(store: FakeObjectStore | None, extract_text: object) -> DocumentService:
    return DocumentService(
        repo=FakeDocumentRepository(),
        store=store,  # type: ignore[arg-type]
        bucket="bap-acme",
        extract_text=extract_text,  # type: ignore[arg-type]
        clock=lambda: _TS,
    )


async def test_upload_extracts_text_and_marks_extracted() -> None:
    store = FakeObjectStore()
    service = _service(store, _succeeding_extractor)

    document = await service.upload(
        owner_id=_OWNER_ID, filename="a.txt", content_type=_TXT_CONTENT_TYPE, data=b"hello world"
    )

    assert document.status == DocumentStatus.EXTRACTED
    assert document.extracted_text == "hello world"
    assert document.extraction_error is None
    assert "bap-acme" in store.buckets
    # object write happens before the row would be visible to a caller
    assert store.puts == [("bap-acme", document.storage_key, b"hello world", _TXT_CONTENT_TYPE)]


async def test_upload_extraction_failure_marks_failed_without_aborting() -> None:
    store = FakeObjectStore()
    service = _service(store, _failing_extractor)

    document = await service.upload(
        owner_id=_OWNER_ID, filename="a.txt", content_type=_TXT_CONTENT_TYPE, data=b"hello"
    )

    assert document.status == DocumentStatus.FAILED
    assert document.extracted_text is None
    assert document.extraction_error == "corrupt file"
    # the object and the row both still exist — only extraction degraded
    assert store.puts  # object was written
    stored = await service.get(document_id=document.id, actor_id=_OWNER_ID, roles=frozenset())
    assert stored.status == DocumentStatus.FAILED


async def test_upload_without_object_store_is_unavailable() -> None:
    service = _service(None, _succeeding_extractor)
    with pytest.raises(ObjectStoreUnavailableError):
        await service.upload(
            owner_id=_OWNER_ID, filename="a.txt", content_type=_TXT_CONTENT_TYPE, data=b"hi"
        )


async def test_get_denies_non_owner_without_elevated_role() -> None:
    store = FakeObjectStore()
    service = _service(store, _succeeding_extractor)
    document = await service.upload(
        owner_id=_OWNER_ID, filename="a.txt", content_type=_TXT_CONTENT_TYPE, data=b"hi"
    )

    with pytest.raises(DocumentAccessDeniedError):
        await service.get(document_id=document.id, actor_id=_OTHER_ID, roles=frozenset())

    # ba may read for triage support
    read = await service.get(
        document_id=document.id, actor_id=_OTHER_ID, roles=frozenset({"ba"})
    )
    assert read.id == document.id


async def test_delete_without_object_store_still_removes_the_row() -> None:
    """Metadata delete must keep working when S3 is disabled (store=None)."""
    service = _service(None, _succeeding_extractor)
    # Seed a row directly (no upload possible without a store).
    document = Document.create(
        owner_id=_OWNER_ID, filename="a.txt", content_type=_TXT_CONTENT_TYPE, size_bytes=3, now=_TS
    )
    await service.repo.add(document)

    await service.delete(document_id=document.id, actor_id=_OWNER_ID, roles=frozenset())

    with pytest.raises(DocumentNotFoundError):
        await service.get(document_id=document.id, actor_id=_OWNER_ID, roles=frozenset())


async def test_list_defaults_to_own_uploads_for_regular_member() -> None:
    store = FakeObjectStore()
    service = _service(store, _succeeding_extractor)
    await service.upload(
        owner_id=_OWNER_ID, filename="mine.txt", content_type=_TXT_CONTENT_TYPE, data=b"a"
    )
    await service.upload(
        owner_id=_OTHER_ID, filename="theirs.txt", content_type=_TXT_CONTENT_TYPE, data=b"b"
    )

    page = await service.list(actor_id=_OWNER_ID, roles=frozenset(), mine=False, limit=20, offset=0)
    assert [d.filename for d in page.items] == ["mine.txt"]

    page_ba = await service.list(
        actor_id=_OWNER_ID, roles=frozenset({"ba"}), mine=False, limit=20, offset=0
    )
    assert {d.filename for d in page_ba.items} == {"mine.txt", "theirs.txt"}


def test_document_create_rejects_unknown_id_argument() -> None:
    # Sanity check that ids are always server-generated (uuid4), never accepted
    # from a caller — Document.create() takes no `id` kwarg at all.
    with pytest.raises(TypeError):
        Document.create(  # type: ignore[call-arg]
            id=uuid4(),
            owner_id=_OWNER_ID,
            filename="a.txt",
            content_type=_TXT_CONTENT_TYPE,
            size_bytes=1,
        )
