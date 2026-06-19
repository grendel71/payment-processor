"""Payment service: idempotency, state machine, ledger, audit, tx scope.

The service does NOT commit. The caller (API route) owns the transaction
boundary and calls session.commit() once the service call returns. Any
exception bubbles up and is rolled back by the FastAPI dependency's
except branch.
"""
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.models.enums import (
    AuditEventType,
    LedgerEntryType,
    PaymentStatus,
)
from app.models.payment import Payment
from app.repositories.audit_event import AuditEventRepository
from app.repositories.exceptions import StaleVersionError
from app.repositories.ledger_entry import LedgerEntryRepository
from app.repositories.payment import PaymentRepository
from app.services.exceptions import (
    ConcurrencyError,
    IdempotencyConflictError,
    InvalidStateTransitionError,
    PaymentNotFoundError,
)


_VALID_SETTLE_FROM = {PaymentStatus.PENDING}


class PaymentService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._payments = PaymentRepository(session)
        self._ledger = LedgerEntryRepository(session)
        self._audit = AuditEventRepository(session)

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------
    def create_payment(
        self,
        *,
        merchant_id: UUID,
        idempotency_key: str,
        amount: int,
    ) -> tuple[Payment, bool]:
        """Create a payment or replay an existing idempotent request.

        Returns (payment, newly_created). On a replay with matching amount,
        returns the existing payment and newly_created=False. On a replay
        with mismatched amount, raises IdempotencyConflictError.
        """
        existing = self._payments.get_by_idempotency(merchant_id, idempotency_key)
        if existing is not None:
            if existing.amount != amount:
                raise IdempotencyConflictError(idempotency_key)
            return existing, False

        payment = self._payments.create(
            id=uuid4(),
            merchant_id=merchant_id,
            idempotency_key=idempotency_key,
            amount=amount,
        )
        self._audit.create(
            payment_id=payment.id,
            event_type=AuditEventType.PAYMENT_CREATED,
            payload={
                "previous_status": None,
                "new_status": PaymentStatus.PENDING.value,
                "requested_amount": amount,
            },
        )
        return payment, True

    # ------------------------------------------------------------------
    # Settle
    # ------------------------------------------------------------------
    def settle_payment(self, payment_id: UUID) -> Payment:
        payment = self._payments.get_by_id(payment_id)
        if payment is None:
            raise PaymentNotFoundError(payment_id)

        if payment.status in (PaymentStatus.SETTLED, PaymentStatus.FAILED):
            # Idempotent: already terminal. Return current state untouched.
            if payment.status == PaymentStatus.SETTLED:
                return payment
            raise InvalidStateTransitionError(
                payment_id, payment.status.value, "settle"
            )

        if payment.status not in _VALID_SETTLE_FROM:
            raise InvalidStateTransitionError(
                payment_id, payment.status.value, "settle"
            )

        previous_status = payment.status.value
        try:
            self._payments.update_with_version(
                payment, status=PaymentStatus.SETTLED
            )
        except StaleVersionError as e:
            raise ConcurrencyError(e.payment_id) from e

        self._ledger.create(
            payment_id=payment.id,
            entry_type=LedgerEntryType.DEBIT,
            amount=payment.amount,
        )
        self._audit.create(
            payment_id=payment.id,
            event_type=AuditEventType.SETTLEMENT_SUCCEEDED,
            payload={
                "previous_status": previous_status,
                "new_status": PaymentStatus.SETTLED.value,
                "requested_amount": payment.amount,
            },
        )
        return payment

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------
    def get_payment_detail(self, payment_id: UUID) -> Payment:
        payment = self._payments.get_by_id(payment_id)
        if payment is None:
            raise PaymentNotFoundError(payment_id)
        return payment
