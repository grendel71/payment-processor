"""Domain exceptions raised by the payment service.

Mapped to HTTP statuses by the API layer:
  PaymentNotFoundError          -> 404
  IdempotencyConflictError      -> 409
  InvalidStateTransitionError   -> 409
  ConcurrencyError              -> 409
"""
from uuid import UUID


class PaymentServiceError(Exception):
    """Base for service-layer errors."""


class PaymentNotFoundError(PaymentServiceError):
    def __init__(self, payment_id: UUID) -> None:
        super().__init__(f"payment {payment_id} not found")
        self.payment_id = payment_id


class IdempotencyConflictError(PaymentServiceError):
    def __init__(self, idempotency_key: str) -> None:
        super().__init__(
            f"idempotency key {idempotency_key!r} already used with different payload"
        )
        self.idempotency_key = idempotency_key


class InvalidStateTransitionError(PaymentServiceError):
    def __init__(self, payment_id: UUID, current: str, attempted: str) -> None:
        super().__init__(
            f"cannot {attempted} payment {payment_id} in status {current!r}"
        )
        self.payment_id = payment_id
        self.current = current
        self.attempted = attempted


class ConcurrencyError(PaymentServiceError):
    def __init__(self, payment_id: UUID) -> None:
        super().__init__(
            f"concurrent modification of payment {payment_id}; retry"
        )
        self.payment_id = payment_id
