# Payments Domain Model Design

**Date:** 2026-05-28
**Status:** approved

---

## 1. Overview

Payment processing domain model supporting idempotent payment creation, immutable ledger, settlement, and mandatory audit events. Built with FastAPI + SQLAlchemy + Pydantic.

## 2. Entities

### Payment

| Field | Type | Notes |
|---|---|---|
| id | UUIDv7 | PK |
| merchant_id | UUID | FK |
| idempotency_key | str(64) | Unique per merchant (enforced by DB unique constraint on `merchant_id + idempotency_key`) |
| amount | int | Minor units (cents) |
| status | enum | `pending`, `settled`, `failed` |
| version | int | Optimistic concurrency control, incremented on each state change |
| created_at | datetime(utc) | |
| updated_at | datetime(utc) | |

### LedgerEntry

| Field | Type | Notes |
|---|---|---|
| id | UUIDv7 | PK |
| payment_id | UUID | FK → Payment.id |
| entry_type | enum | `DEBIT` |
| amount | int | Always equals Payment.amount |
| created_at | datetime(utc) | Immutable — never updated or deleted |

### AuditEvent

| Field | Type | Notes |
|---|---|---|
| id | UUIDv7 | PK |
| payment_id | UUID | FK → Payment.id |
| event_type | enum | `PAYMENT_CREATED`, `SETTLEMENT_INITIATED`, `SETTLEMENT_SUCCEEDED`, `SETTLEMENT_FAILED` |
| payload | JSON | Context: previous_status, new_status, requested_amount, error_message if failed |
| created_at | datetime(utc) | Immutable — never updated or deleted |

### Settlement

| Field | Type | Notes |
|---|---|---|
| id | UUIDv7 | PK |
| status | enum | `open`, `closed` |
| total_amount | int | Aggregated sum of settled payments |
| settled_at | datetime(utc) | nullable |
| created_at | datetime(utc) | |

### SettlementPayment (join table)

| Field | Type | Notes |
|---|---|---|
| settlement_id | UUID | FK → Settlement.id |
| payment_id | UUID | FK → Payment.id, unique (a payment belongs to one settlement) |

## 3. Relationships

```
Payment 1──N LedgerEntry       (DEBIT entry written on settlement)
Payment 1──N AuditEvent        (event logged on every state transition)
Settlement N──M Payment        (via SettlementPayment join)
```

- `Payment` is the aggregate root.
- `LedgerEntry` and `AuditEvent` are append-only children.
- `Settlement` is a grouping wrapper, not modeled in API v1 but schema-ready.

## 4. State Machine

```
pending ──settle✓──→ settled
pending ──settle✗──→ failed
```

- `pending`: payment created, not yet settled.
- `settled`: settlement succeeded.
- `failed`: settlement failed.
- Valid transitions: `pending → settled`, `pending → failed`.
- Invalid transitions (e.g. `settled → pending`, `failed → settled`) → 409 Conflict.

## 5. Idempotency

- Uniqueness constraint: DB unique index on `(merchant_id, idempotency_key)`.
- Client supplies `Idempotency-Key` header on `POST /payments` and `POST /payments/{id}/settle`.
- Duplicate `POST /payments` with same key → returns existing Payment (200 OK), no side effects.
- Duplicate `POST /payments/{id}/settle` → idempotent: returns current Payment state (200 OK), no state transition if already settled.

## 6. Immutable Ledger

- `LedgerEntry` rows are INSERT-only. No UPDATE or DELETE.
- Written inside the same DB transaction as the payment state transition.
- Retrieved as read-only via API.

## 7. Audit Events

- Every state transition writes an `AuditEvent` in the same DB transaction.
- `event_type` captures the action, `payload` captures the before/after state.
- Retrieved as read-only via API (embedded in `GET /payments/{id}` response).

## 8. Failure Scenarios

| Scenario | Behavior |
|---|---|
| Duplicate payment creation | Returns existing Payment (200). No new ledger or audit events. |
| Duplicate settlement | Idempotent: returns current state (200). If already settled, no change. |
| Settlement of non-pending payment | 409 Conflict. |
| Concurrent settlement attempts | Version check fails on second write → 409 Conflict, caller retries. |
| DB failure during settlement | Transaction rolls back. Ledger and audit not written. Payment stays pending. |
| Audit write failure | Transaction rolls back. State change not committed. |

## 9. API Routes

| Method | Path | Request/Response |
|---|---|---|
| `POST` | `/payments` | Request: `{merchant_id, amount}`, Header: `Idempotency-Key`. Response: `PaymentDetailResponse` (201) |
| `GET` | `/payments/{id}` | Response: `PaymentDetailResponse` (200) — includes ledger entries and audit events |
| `POST` | `/payments/{id}/settle` | Request: `{}`, Header: `Idempotency-Key`. Response: `PaymentDetailResponse` (200) |
| `GET` | `/healthz` | Response: `{"status":"ok"}` (200) |

## 10. Pydantic Models

```python
PaymentCreate   — merchant_id: UUID, amount: int (gt=0)
PaymentResponse — id, merchant_id, idempotency_key, amount, status, version, created_at, updated_at
LedgerEntryResponse — id, payment_id, entry_type, amount, created_at
AuditEventResponse  — id, payment_id, event_type, payload, created_at
SettleRequest       — (empty body, idempotency key from header)
PaymentDetailResponse — PaymentResponse + ledger_entries: list[LedgerEntryResponse] + audit_events: list[AuditEventResponse]
```

## 11. SQLAlchemy Models

```python
Payment         — maps to payments table
LedgerEntry     — maps to ledger_entries table
AuditEvent      — maps to audit_events table
Settlement      — maps to settlements table (v1 schema only)
SettlementPayment — maps to settlement_payments join table (v1 schema only)
```

## 12. Directory Structure

```
app/
├── models/
│   ├── __init__.py
│   ├── payment.py          # SQLAlchemy ORM models
│   ├── ledger_entry.py
│   ├── audit_event.py
│   └── settlement.py
├── schemas/
│   ├── __init__.py
│   ├── payment.py          # Pydantic request/response schemas
│   ├── ledger_entry.py
│   └── audit_event.py
├── repositories/
│   ├── __init__.py
│   ├── payment.py          # Data access for payments
│   ├── ledger_entry.py
│   └── audit_event.py
├── services/
│   ├── __init__.py
│   └── payment.py          # Business logic: idempotency, state machine, ledger, audit
├── api/
│   ├── __init__.py
│   ├── payments.py         # Route handlers
│   └── health.py           # Health check
├── db.py                   # Engine, session factory, Base
└── main.py                 # FastAPI app, lifespan
```

## 13. Risks & Open Items

- **No merchant model yet** — `merchant_id` is a UUID without FK validation. Add Merchant model when needed.
- **Single currency** — amount field is int (cents) with no currency column. Schema is backward-compatible to add currency later.
- **Settlement linkage** — Settlement and SettlementPayment tables exist in schema but no API yet. Designed for batch reconciliation workflows.
- **No refund flow** — Refund states and endpoints are out of v1 scope but the entity model supports adding an `original_payment_id` FK later.
