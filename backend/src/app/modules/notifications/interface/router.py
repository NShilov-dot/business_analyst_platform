"""FastAPI router for /api/v1/notifications.

Access gate: any authenticated tenant member — every caller reads their OWN
feed, derived from their ticket involvement.  No role check: `ba` widens what
a user is shown, it never grants access to someone else's feed.

Rate limiting and CSRF checks follow the same pattern as the other routers
(check_csrf no-ops on GET; it guards POST /read).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.deps import PrincipalDep, SessionDep, check_csrf, check_rate_limit
from app.modules.notifications.application.dtos import NotificationsQuery
from app.modules.notifications.application.services import NotificationsService
from app.modules.notifications.infrastructure.repositories import (
    SqlNotificationsRepository,
)
from app.modules.notifications.interface.schemas import (
    Envelope,
    MarkReadRequest,
    MarkReadResponse,
    NotificationFeedResponse,
)

router = APIRouter(
    prefix="/notifications",
    tags=["notifications"],
    dependencies=[Depends(check_rate_limit), Depends(check_csrf)],
)


async def _service(session: SessionDep) -> NotificationsService:
    return NotificationsService(repo=SqlNotificationsRepository(session))


ServiceDep = Annotated[NotificationsService, Depends(_service)]


# ---------------------------------------------------------------------------
# GET /api/v1/notifications
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=Envelope[NotificationFeedResponse],
    summary="My ticket notifications (unread only by default) with unread count",
)
async def list_notifications(
    principal: PrincipalDep,
    service: ServiceDep,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    include_read: Annotated[
        bool,
        Query(description="Also return events the caller already dismissed"),
    ] = False,
) -> Envelope[NotificationFeedResponse]:
    feed = await service.feed(
        subject=principal.subject,
        roles=frozenset(principal.roles),
        query=NotificationsQuery(limit=limit, include_read=include_read),
    )
    return Envelope(data=NotificationFeedResponse.from_dto(feed))


# ---------------------------------------------------------------------------
# POST /api/v1/notifications/read
# ---------------------------------------------------------------------------


@router.post(
    "/read",
    response_model=Envelope[MarkReadResponse],
    summary="Dismiss notifications",
)
async def mark_read(
    body: MarkReadRequest,
    principal: PrincipalDep,
    service: ServiceDep,
) -> Envelope[MarkReadResponse]:
    await service.mark_read(subject=principal.subject, entry_ids=body.entry_ids)
    return Envelope(data=MarkReadResponse(marked=len(body.entry_ids)))
