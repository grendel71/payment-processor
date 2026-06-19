"""Payment aggregate root ORM model."""
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import Enum as SAEnum, Integer, String, UniqueConstraint
from sqlalchemy import Uuid as SA_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.enums import PaymentStatus


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint(
            "merchant_id",
            "idempotency_key",
            name="uq_merchant_idempotency",
        ),
    )

    id: Mapped[UUID] = mapped_column(SA_UUID(as_uuid=True), primary_key=True)
    merchant_id: Mapped[UUID] = mapped_column(SA_UUID(as_uuid=True), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[PaymentStatus] = mapped_column(
        SAEnum(
            PaymentStatus,
            name="payment_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=PaymentStatus.PENDING,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow, nullable=False)

    ledger_entries: Mapped[list["LedgerEntry"]] = relationship(  # noqa: F821
        back_populates="payment",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    audit_events: Mapped[list["AuditEvent"]] = relationship(  # noqa: F821
        back_populates="payment",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
