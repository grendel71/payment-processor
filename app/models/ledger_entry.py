"""Immutable ledger entry ORM model.

INSERT-only by contract. No application code path issues UPDATE or DELETE
against this table; ledger integrity is enforced in the service layer.
"""
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import Enum as SAEnum, ForeignKey, Integer
from sqlalchemy import Uuid as SA_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.enums import LedgerEntryType


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"

    id: Mapped[UUID] = mapped_column(SA_UUID(as_uuid=True), primary_key=True)
    payment_id: Mapped[UUID] = mapped_column(
        SA_UUID(as_uuid=True),
        ForeignKey("payments.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    entry_type: Mapped[LedgerEntryType] = mapped_column(
        SAEnum(
            LedgerEntryType,
            name="ledger_entry_type",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        nullable=False,
    )

    payment: Mapped["Payment"] = relationship(back_populates="ledger_entries")  # noqa: F821
