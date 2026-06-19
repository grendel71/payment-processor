"""Payment repository: data access for the Payment aggregate root."""
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models.enums import PaymentStatus
from app.models.payment import Payment


class PaymentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        id: UUID,
        merchant_id: UUID,
        idempotency_key: str,
        amount: int,
    ) -> Payment:
        payment = Payment(
            id=id,
            merchant_id=merchant_id,
            idempotency_key=idempotency_key,
            amount=amount,
            status=PaymentStatus.PENDING,
            version=1,
        )
        self._session.add(payment)
        self._session.flush()
        return payment

    def get_by_id(self, payment_id: UUID) -> Payment | None:
        return self._session.get(Payment, payment_id)

    def get_by_idempotency(
        self,
        merchant_id: UUID,
        idempotency_key: str,
    ) -> Payment | None:
        return (
            self._session.query(Payment)
            .filter_by(merchant_id=merchant_id, idempotency_key=idempotency_key)
            .one_or_none()
        )

    def update_with_version(
        self,
        payment: Payment,
        *,
        status: PaymentStatus,
    ) -> Payment:
        """Optimistic-concurrency update.

        Issues an UPDATE filtered on the current `version`; if zero rows
        match, another writer moved first and the caller must retry.
        Raises StaleVersionError when the expected version is no longer
        current.
        """
        from app.repositories.exceptions import StaleVersionError

        result = (
            update(Payment)
            .where(Payment.id == payment.id, Payment.version == payment.version)
            .values(status=status, version=payment.version + 1)
        )
        matched = self._session.execute(result).rowcount or 0
        if matched == 0:
            raise StaleVersionError(payment.id, payment.version)
        self._session.flush()
        self._session.refresh(payment)
        return payment
