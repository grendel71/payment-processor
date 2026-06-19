"""Immutable audit event ORM model.

Every state transition on a Payment produces one AuditEvent in the same
DB transaction. INSERT-only by contract.
"""
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import Enum as SAEnum, ForeignKey
from sqlalchemy import Uuid as SA_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db import Base
from app.models.enums import AuditEventType


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[UUID] = mapped_column(SA_UUID(as_uuid=True), primary_key=True)
    payment_id: Mapped[UUID] = mapped_column(
        SA_UUID(as_uuid=True),
        ForeignKey("payments.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[AuditEventType] = mapped_column(
        SAEnum(
            AuditEventType,
            name="audit_event_type",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        nullable=False,
    )

    payment: Mapped["Payment"] = relationship(back_populates="audit_events")  # noqa: F821
