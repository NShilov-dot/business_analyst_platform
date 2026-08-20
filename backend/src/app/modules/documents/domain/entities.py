"""Document domain entity — per-tenant document library feeding the AI intake dialog.

No SQLAlchemy / Pydantic / FastAPI imports allowed in this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from app.modules.documents.domain.errors import (
    DocumentTooLargeError,
    DocumentValidationError,
    UnsupportedDocumentTypeError,
)

FILENAME_MAX = 255
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MiB — the upload route enforces this too
EXTRACTED_TEXT_CAP = 200_000  # characters; 1:1 with the document, no per-turn re-read
EXTRACTION_ERROR_MAX = 2_000

ALLOWED_CONTENT_TYPES = frozenset(
    {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/plain",
        "text/markdown",
    }
)


class DocumentStatus(StrEnum):
    UPLOADED = "uploaded"
    EXTRACTED = "extracted"
    FAILED = "failed"


def _now() -> datetime:
    return datetime.now(UTC)


def _sanitize_filename(filename: str) -> str:
    # Display-only metadata (storage_key is server-generated, never derived from
    # this), but still strip any path components a careless/hostile client
    # might smuggle into the multipart filename field.
    name = filename.strip().replace("\\", "/").rsplit("/", maxsplit=1)[-1]
    if not name:
        raise DocumentValidationError("Filename cannot be empty")
    return name[:FILENAME_MAX]


@dataclass(slots=True, kw_only=True)
class Document:
    """Aggregate root. ``storage_key`` is server-generated (``documents/{id}``)
    — never derived from user input, closing the path-traversal boundary."""

    id: UUID
    owner_id: UUID
    filename: str
    content_type: str
    size_bytes: int
    storage_key: str
    status: DocumentStatus
    extracted_text: str | None
    extraction_error: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def create(
        cls,
        *,
        owner_id: UUID,
        filename: str,
        content_type: str,
        size_bytes: int,
        now: datetime | None = None,
    ) -> Document:
        if content_type not in ALLOWED_CONTENT_TYPES:
            raise UnsupportedDocumentTypeError(f"Unsupported content type: {content_type!r}")
        if size_bytes <= 0:
            raise DocumentValidationError("Uploaded file is empty")
        if size_bytes > MAX_FILE_SIZE_BYTES:
            raise DocumentTooLargeError(
                f"File exceeds the {MAX_FILE_SIZE_BYTES // (1024 * 1024)} MiB limit"
            )
        ts = now or _now()
        doc_id = uuid4()
        return cls(
            id=doc_id,
            owner_id=owner_id,
            filename=_sanitize_filename(filename),
            content_type=content_type,
            size_bytes=size_bytes,
            storage_key=f"documents/{doc_id}",
            status=DocumentStatus.UPLOADED,
            extracted_text=None,
            extraction_error=None,
            created_at=ts,
            updated_at=ts,
        )

    def mark_extracted(self, text: str, *, now: datetime | None = None) -> None:
        self.status = DocumentStatus.EXTRACTED
        self.extracted_text = text[:EXTRACTED_TEXT_CAP]
        self.extraction_error = None
        self.updated_at = now or _now()

    def mark_failed(self, error: str, *, now: datetime | None = None) -> None:
        self.status = DocumentStatus.FAILED
        self.extracted_text = None
        self.extraction_error = error[:EXTRACTION_ERROR_MAX]
        self.updated_at = now or _now()
