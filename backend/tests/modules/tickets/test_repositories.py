"""Unit tests for SqlAlchemyTicketRepository error translation (no DB needed)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.modules.tickets.domain.entities import AttestationKind, GateAttestation
from app.modules.tickets.domain.errors import AttestationDuplicateError
from app.modules.tickets.infrastructure.repositories import SqlAlchemyTicketRepository


class _Orig(Exception):
    """Stand-in for the asyncpg DBAPI error, carrying a Postgres sqlstate."""

    def __init__(self, sqlstate: str) -> None:
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


class _FlushSession:
    """Fake AsyncSession whose flush() raises an IntegrityError with a given sqlstate."""

    def __init__(self, sqlstate: str) -> None:
        self._sqlstate = sqlstate

    def add(self, _row: Any) -> None:
        pass

    async def flush(self) -> None:
        raise IntegrityError("INSERT ...", {}, _Orig(self._sqlstate))


def _formal_dod(cycle: int = 1) -> GateAttestation:
    return GateAttestation(
        id=uuid4(),
        ticket_id=uuid4(),
        kind=AttestationKind.FORMAL_DOD,
        acceptance_cycle=cycle,
        attested_by="ba-sub",
        roles_snapshot=["ba"],
        checklist={"done": True},
        spec_ref=None,
        agreed_with_subject=None,
        comment=None,
        attested_at=datetime(2026, 7, 9, tzinfo=UTC),
    )


async def test_add_attestation_maps_unique_violation_to_duplicate() -> None:
    repo = SqlAlchemyTicketRepository(_FlushSession("23505"))  # type: ignore[arg-type]
    with pytest.raises(AttestationDuplicateError):
        await repo.add_attestation(_formal_dod())


async def test_add_attestation_reraises_non_unique_integrity_error() -> None:
    # An FK violation (23503) is a real bug, not a duplicate — must not be masked.
    repo = SqlAlchemyTicketRepository(_FlushSession("23503"))  # type: ignore[arg-type]
    with pytest.raises(IntegrityError):
        await repo.add_attestation(_formal_dod())
