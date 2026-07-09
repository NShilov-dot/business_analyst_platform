"""FastAPI router for /v1/analytics.

Access gate: any authenticated tenant member (PrincipalDep is required as
auth guard; no additional role check — tickets are org-wide readable).

Read-only: GET endpoints only.
Rate limiting and CSRF checks follow the same pattern as other routers.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.deps import PrincipalDep, SessionDep, check_csrf, check_rate_limit
from app.modules.analytics.application.dtos import ActivityQuery, TicketFlowQuery
from app.modules.analytics.application.services import AnalyticsService
from app.modules.analytics.infrastructure.repositories import SqlAnalyticsRepository
from app.modules.analytics.interface.schemas import (
    ActivityItemResponse,
    Envelope,
    TicketFlowResponse,
)

router = APIRouter(
    prefix="/analytics",
    tags=["analytics"],
    dependencies=[Depends(check_rate_limit), Depends(check_csrf)],
)


async def _service(session: SessionDep) -> AnalyticsService:
    return AnalyticsService(repo=SqlAnalyticsRepository(session))


ServiceDep = Annotated[AnalyticsService, Depends(_service)]


# ---------------------------------------------------------------------------
# GET /v1/analytics/ticket-flow
# ---------------------------------------------------------------------------


@router.get(
    "/ticket-flow",
    response_model=Envelope[TicketFlowResponse],
    summary="Weekly ticket creation and closure counts for the last N weeks",
)
async def get_ticket_flow(
    _: PrincipalDep,
    service: ServiceDep,
    weeks: Annotated[int, Query(ge=1, le=52)] = 24,
) -> Envelope[TicketFlowResponse]:
    flow = await service.ticket_flow(query=TicketFlowQuery(weeks=weeks))
    return Envelope(data=TicketFlowResponse.from_dto(flow))


# ---------------------------------------------------------------------------
# GET /v1/analytics/activity
# ---------------------------------------------------------------------------


@router.get(
    "/activity",
    response_model=Envelope[list[ActivityItemResponse]],
    summary="Most-recent ticket lifecycle audit events",
)
async def get_activity(
    _: PrincipalDep,
    service: ServiceDep,
    limit: Annotated[int, Query(ge=1, le=50)] = 8,
) -> Envelope[list[ActivityItemResponse]]:
    items = await service.ticket_activity(query=ActivityQuery(limit=limit))
    return Envelope(data=[ActivityItemResponse.from_dto(i) for i in items])
