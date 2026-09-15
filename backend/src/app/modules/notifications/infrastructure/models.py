"""SQLAlchemy ORM rows for the notifications module.

Only ONE table is owned here: which events a user has dismissed.  The
notification feed itself is NOT stored — it is derived on read from
`audit_entries` joined to ticket participation (see PRODUCT_MODULES §5: pull
model for Phases 1-2).

Deriving rather than fanning out at write time is deliberate:
- the EventBus is fail-closed and shares the request transaction, so a bug in
  notification fan-out would break ticket writes;
- a user who becomes a participant later (assignment, triage) sees the full
  prior history with no backfill;
- `audit_entries` is complete from day zero, so the feed works on existing
  tickets the moment this ships.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class NotificationReadRow(Base):
    """One dismissed event. Present = this user has read that audit entry.

    Per item, not a watermark: clicking one notification must clear exactly it
    and leave older unread events unread.
    """

    __tablename__ = "notification_reads"
    __table_args__ = ({"info": {"tenant_scope": "tenant"}},)

    subject: Mapped[str] = mapped_column(String(255), primary_key=True)
    entry_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    read_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
