Payments domain:

Entities:
- Payment
- LedgerEntry
- SettlementJob
- AuditEvent

Statuses:
- pending
- authorized
- settled
- failed

Rules:
- idempotency required
- ledger immutable
- audit events mandatory
