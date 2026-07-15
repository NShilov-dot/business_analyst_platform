"""Pydantic request/response schemas for the directory API.

Follows the Envelope[T] pattern used across all other interface layers in this
codebase (see audit/interface/schemas.py).  Local copy — avoids cross-module
schema imports.
"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict

from app.modules.directory.application.dtos import DirectoryUser

T = TypeVar("T")


class Envelope(BaseModel, Generic[T]):
    """Single-item or list response wrapper: ``{"data": <payload>}``."""

    data: T


class DirectoryUserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    subject: str
    username: str
    first_name: str | None
    last_name: str | None
    full_name: str
    email: str | None

    @classmethod
    def from_dto(cls, dto: DirectoryUser) -> DirectoryUserResponse:
        return cls(
            subject=dto.subject,
            username=dto.username,
            first_name=dto.first_name,
            last_name=dto.last_name,
            full_name=dto.full_name,
            email=dto.email,
        )
