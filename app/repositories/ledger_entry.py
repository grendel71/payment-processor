"""Ledger entry repository: append-only access."""
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.models.enums import LedgerEntryType
from app.models.ledger_entry import LedgerEntry


class LedgerEntryRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        payment_id: UUID,
        entry_type: LedgerEntryType,
        amount: int,
    ) -> LedgerEntry:
        entry = LedgerEntry(
            id=uuid4(),
            payment_id=payment_id,
            entry_type=entry_type,
            amount=amount,
        )
        self._session.add(entry)
        self._session.flush()
        return entry

    def get_by_payment_id(self, payment_id: UUID) -> list[LedgerEntry]:
        return (
            self._session.query(LedgerEntry)
            .filter_by(payment_id=payment_id)
            .order_by(LedgerEntry.created_at)
            .all()
        )
