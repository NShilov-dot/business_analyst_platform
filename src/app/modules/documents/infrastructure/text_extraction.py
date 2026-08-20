"""Document text extraction: PDF (pypdf), DOCX (python-docx), TXT/MD (utf-8).

Sync + CPU-bound on purpose — callers (DocumentService) run this via
`asyncio.to_thread`. Every parser failure (corrupt file, wrong magic bytes,
encrypted PDF, unsupported type) is wrapped in TextExtractionError so the
caller can degrade the upload to status=failed instead of aborting it.
"""

from __future__ import annotations

import io

import docx
from pypdf import PdfReader
from pypdf.errors import PdfReadError

_MAX_CHARS = 200_000  # mirrors domain.entities.EXTRACTED_TEXT_CAP
_MAX_PDF_PAGES = 200
_PDF_MAGIC = b"%PDF-"
_DOCX_MAGIC = b"PK\x03\x04"  # docx is a zip container (local-file header)

_DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class TextExtractionError(Exception):
    """Raised on any parser failure. Caught by DocumentService.upload — never
    propagates past it, so a bad file degrades the document to `failed`
    rather than aborting the upload."""


def extract_text(content_type: str, filename: str, data: bytes) -> str:
    if content_type == "application/pdf":
        return _extract_pdf(data)
    if content_type == _DOCX_CONTENT_TYPE:
        return _extract_docx(data)
    if content_type in {"text/plain", "text/markdown"}:
        return data.decode("utf-8", errors="replace")[:_MAX_CHARS]
    raise TextExtractionError(f"No extractor for content type {content_type!r}")


def _extract_pdf(data: bytes) -> str:
    if not data.startswith(_PDF_MAGIC):
        raise TextExtractionError("File does not look like a PDF (bad magic bytes)")
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise TextExtractionError("PDF is password-protected")
        chunks: list[str] = []
        total = 0
        for page in reader.pages[:_MAX_PDF_PAGES]:
            text = page.extract_text() or ""
            chunks.append(text)
            total += len(text)
            if total >= _MAX_CHARS:
                break
        return "\n".join(chunks)[:_MAX_CHARS]
    except TextExtractionError:
        raise
    except (PdfReadError, ValueError, KeyError, OSError) as exc:
        raise TextExtractionError(f"Failed to parse PDF: {exc}") from exc


def _extract_docx(data: bytes) -> str:
    if not data.startswith(_DOCX_MAGIC):
        raise TextExtractionError("File does not look like a DOCX (bad magic bytes)")
    try:
        document = docx.Document(io.BytesIO(data))
        text = "\n".join(p.text for p in document.paragraphs)
        return text[:_MAX_CHARS]
    except Exception as exc:
        raise TextExtractionError(f"Failed to parse DOCX: {exc}") from exc
