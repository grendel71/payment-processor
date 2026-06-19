"""Repository-level exceptions."""
from uuid import UUID


class StaleVersionError(Exception):
    """Optimistic-concurrency check failed: row version no longer current."""

    def __init__(self, payment_id: UUID, expected_version: int) -> None:
        super().__init__(
            f"payment {payment_id} version {expected_version} no longer current"
        )
        self.payment_id = payment_id
        self.expected_version = expected_version
