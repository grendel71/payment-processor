"""Domain enums for the payments domain model."""
from enum import Enum


class PaymentStatus(str, Enum):
    PENDING = "pending"
    SETTLED = "settled"
    FAILED = "failed"


class LedgerEntryType(str, Enum):
    DEBIT = "debit"


class AuditEventType(str, Enum):
    PAYMENT_CREATED = "payment_created"
    SETTLEMENT_INITIATED = "settlement_initiated"
    SETTLEMENT_SUCCEEDED = "settlement_succeeded"
    SETTLEMENT_FAILED = "settlement_failed"
