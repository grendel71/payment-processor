"""Settlement + SettlementPayment join table.

Schema-ready only: tables exist for future batch reconciliation workflows
but no v1 API route reads or writes them. Intentionally minimal.
"""
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import Column, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy import Uuid as SA_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Settlement(Base):
    __tablename__ = "settlements"

    id: Mapped[UUID] = mapped_column(SA_UUID(as_uuid=True), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    total_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    settled_at: Mapped[datetime | None] = mapped_column(default=None, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        nullable=False,
    )


class SettlementPayment(Base):
    __tablename__ = "settlement_payments"
    __table_args__ = (
        UniqueConstraint("payment_id", name="uq_settlement_payment_payment"),
    )

    settlement_id: Mapped[UUID] = mapped_column(
        SA_UUID(as_uuid=True),
        ForeignKey("settlements.id", ondelete="CASCADE"),
        primary_key=True,
    )
    payment_id: Mapped[UUID] = mapped_column(
        SA_UUID(as_uuid=True),
        ForeignKey("payments.id", ondelete="RESTRICT"),
        nullable=False,
    )
