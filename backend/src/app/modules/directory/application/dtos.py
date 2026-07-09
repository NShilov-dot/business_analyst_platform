"""Data transfer objects for the directory module.

Plain frozen dataclasses — no framework imports. Used as the boundary between
the application layer and the interface (Pydantic) layer.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DirectoryUser:
    """A single user record returned by the directory."""

    subject: str
    username: str
    first_name: str | None
    last_name: str | None
    full_name: str
    email: str | None


@dataclass(frozen=True, slots=True)
class ListDirectoryUsersQuery:
    """Query parameters for listing directory users."""

    search: str | None
    limit: int
