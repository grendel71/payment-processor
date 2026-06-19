"""Audit event repository: append-only access."""
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.models.audit_event import AuditEvent
from app.models.enums import AuditEventType


class AuditEventRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        payment_id: UUID,
        event_type: AuditEventType,
        payload: dict[str, Any],
    ) -> AuditEvent:
        event = AuditEvent(
            id=uuid4(),
            payment_id=payment_id,
            event_type=event_type,
            payload=payload,
        )
        self._session.add(event)
        self._session.flush()
        return event

    def get_by_payment_id(self, payment_id: UUID) -> list[AuditEvent]:
        return (
            self._session.query(AuditEvent)
            .filter_by(payment_id=payment_id)
            .order_by(AuditEvent.created_at)
            .all()
        )
