from __future__ import annotations

from typing import NamedTuple

from app.modules.documents.domain.entities import Document


class Page(NamedTuple):
    items: list[Document]
    total: int
    limit: int
    offset: int
