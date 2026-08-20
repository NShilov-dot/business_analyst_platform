"""Self-checks for text_extraction.extract_text on tiny real files + corrupt input."""

from __future__ import annotations

import io

import pytest
from docx import Document as DocxDocument
from pypdf import PdfWriter

from app.modules.documents.infrastructure.text_extraction import TextExtractionError, extract_text

_DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _tiny_pdf() -> bytes:
    buf = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.write(buf)
    return buf.getvalue()


def _tiny_docx(text: str) -> bytes:
    buf = io.BytesIO()
    document = DocxDocument()
    document.add_paragraph(text)
    document.save(buf)
    return buf.getvalue()


def test_extract_text_pdf_parses_without_error() -> None:
    result = extract_text("application/pdf", "a.pdf", _tiny_pdf())
    assert isinstance(result, str)


def test_extract_text_docx_returns_paragraph_text() -> None:
    result = extract_text(_DOCX_CONTENT_TYPE, "a.docx", _tiny_docx("Hello from docx"))
    assert "Hello from docx" in result


def test_extract_text_txt_decodes_utf8() -> None:
    assert extract_text("text/plain", "a.txt", "Привет мир".encode()) == "Привет мир"


def test_extract_text_md_decodes_utf8() -> None:
    assert extract_text("text/markdown", "a.md", b"# Title\nbody") == "# Title\nbody"


def test_extract_text_bad_pdf_magic_raises() -> None:
    with pytest.raises(TextExtractionError):
        extract_text("application/pdf", "bad.pdf", b"not a real pdf")


def test_extract_text_pdf_with_magic_but_corrupt_body_raises() -> None:
    with pytest.raises(TextExtractionError):
        extract_text("application/pdf", "bad.pdf", b"%PDF-1.4\nnot really a pdf body")


def test_extract_text_bad_docx_magic_raises() -> None:
    with pytest.raises(TextExtractionError):
        extract_text(_DOCX_CONTENT_TYPE, "bad.docx", b"not a docx")


def test_extract_text_unsupported_content_type_raises() -> None:
    with pytest.raises(TextExtractionError):
        extract_text("application/zip", "a.zip", b"PK\x03\x04")
