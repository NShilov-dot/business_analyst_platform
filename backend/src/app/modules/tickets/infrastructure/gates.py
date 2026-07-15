"""Phase-1 gate adapters backed by GateAttestation records.

- AttestationSpecApprovalGate: satisfied iff a spec_approved attestation
  (cycle 0) exists.  MVP will swap in RequirementsSpecApprovalGate
  (ArtifactStatus == Approved) behind the same port.
- AttestationAcceptanceGate: satisfied iff BOTH formal_dod and business_value
  attestations exist for the ticket's CURRENT acceptance cycle.
"""

from __future__ import annotations

from uuid import UUID

from app.modules.tickets.domain.entities import AttestationKind, GateCheckResult
from app.modules.tickets.domain.ports import TicketRepository


class AttestationSpecApprovalGate:
    """SpecApprovalGate adapter over the attestation store."""

    def __init__(self, repo: TicketRepository) -> None:
        self._repo = repo

    async def is_satisfied(self, ticket_id: UUID) -> GateCheckResult:
        attestation = await self._repo.get_attestation(
            ticket_id, AttestationKind.SPEC_APPROVED, 0
        )
        if attestation is None:
            return GateCheckResult.failed(
                "spec_approved attestation (cycle 0) is missing"
            )
        return GateCheckResult.passed()


class AttestationAcceptanceGate:
    """AcceptanceGate adapter over the attestation store.

    WHO signed is enforced by TicketService at attestation time — this gate
    only checks presence for the given cycle.
    """

    def __init__(self, repo: TicketRepository) -> None:
        self._repo = repo

    async def is_satisfied(
        self, ticket_id: UUID, *, acceptance_cycle: int
    ) -> GateCheckResult:
        reasons: list[str] = []
        formal = await self._repo.get_attestation(
            ticket_id, AttestationKind.FORMAL_DOD, acceptance_cycle
        )
        if formal is None:
            reasons.append(
                f"formal_dod attestation for cycle {acceptance_cycle} is missing"
            )
        value = await self._repo.get_attestation(
            ticket_id, AttestationKind.BUSINESS_VALUE, acceptance_cycle
        )
        if value is None:
            reasons.append(
                f"business_value attestation for cycle {acceptance_cycle} is missing"
            )
        if reasons:
            return GateCheckResult.failed(*reasons)
        return GateCheckResult.passed()
