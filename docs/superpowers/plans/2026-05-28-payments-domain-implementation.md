# Payments Domain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the payments domain model with idempotent payment creation, immutable ledger entries, settlement states, and audit events.

**Architecture:** Use Approach 1: thin Pydantic/SQLAlchemy models with business rules enforced in a service layer. FastAPI routes stay thin, repositories isolate SQLAlchemy access, and the `PaymentService` owns idempotency, state transitions, ledger writes, audit writes, and transaction boundaries.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy, pytest, FastAPI TestClient, SQLite for local/test execution.

---

## Confirmed Scope

Implement only these API endpoints:

```text
POST /payments
GET  /payments/{id}
POST /payments/{id}/settle
GET  /healthz
```

The v1 state machine is intentionally simple:

```text
pending --settle success--> settled
pending --settle failure--> failed
```

No `/authorize` endpoint in v1. No refund endpoint in v1. No settlement listing endpoint in v1.

## Domain Decisions

- Idempotency keys are unique per merchant using `(merchant_id, idempotency_key)`.
- Amounts are stored as integer minor units for a single currency.
- `LedgerEntry` and `AuditEvent` are append-only.
- `Payment` is the aggregate root.
- `Settlement` and `SettlementPayment` are schema-ready only. They are not exposed through v1 API routes.
- `POST /payments` creates a `pending` payment and writes a `PAYMENT_CREATED` audit event.
- `POST /payments/{id}/settle` settles a `pending` payment, writes one ledger entry, and writes a settlement audit event.
- Duplicate `POST /payments` with the same merchant, key, and amount returns the existing payment.
- Duplicate `POST /payments` with the same merchant/key but different amount returns `409 Conflict`.
- Duplicate settle of an already settled payment returns the current settled payment without writing duplicate ledger rows.

## File Structure

```text
app/
├── __init__.py
├── db.py
├── main.py
├── models/
│   ├── __init__.py
│   ├── enums.py
│   ├── payment.py
│   ├── ledger_entry.py
│   ├── audit_event.py
│   └── settlement.py
├── schemas/
│   ├── __init__.py
│   ├── payment.py
│   ├── ledger_entry.py
│   └── audit_event.py
├── repositories/
│   ├── __init__.py
│   ├── payment.py
│   ├── ledger_entry.py
│   └── audit_event.py
├── services/
│   ├── __init__.py
│   ├── exceptions.py
│   └── payment.py
└── api/
    ├── __init__.py
    ├── deps.py
    ├── health.py
    └── payments.py
```

## Task 1: Database Setup And Enums

**Files:**
- Create: `app/__init__.py`
- Create: `app/db.py`
- Create: `app/models/__init__.py`
- Create: `app/models/enums.py`
- Test: `tests/test_enums.py`

- [ ] Write failing enum tests for `PaymentStatus`, `LedgerEntryType`, and `AuditEventType`.
- [ ] Run `pytest tests/test_enums.py -v` and confirm import failure.
- [ ] Add SQLAlchemy `Base`, `engine`, `SessionLocal`, and `get_db()` in `app/db.py`.
- [ ] Add enums: `pending`, `settled`, `failed`, `debit`, `payment_created`, `settlement_initiated`, `settlement_succeeded`, `settlement_failed`.
- [ ] Run `pytest tests/test_enums.py -v` and confirm pass.
- [ ] Commit: `feat: add database config and domain enums`.

## Task 2: SQLAlchemy Models

**Files:**
- Create: `app/models/payment.py`
- Create: `app/models/ledger_entry.py`
- Create: `app/models/audit_event.py`
- Create: `app/models/settlement.py`
- Test: `tests/test_models.py`

- [ ] Write failing model tests for table creation, payment columns, idempotency unique constraint, ledger FK, audit FK, and settlement tables.
- [ ] Run `pytest tests/test_models.py -v` and confirm imports fail.
- [ ] Implement `Payment` with `id`, `merchant_id`, `idempotency_key`, `amount`, `status`, `version`, `created_at`, `updated_at`.
- [ ] Add unique constraint `uq_merchant_idempotency` on `(merchant_id, idempotency_key)`.
- [ ] Implement `LedgerEntry` with `id`, `payment_id`, `entry_type`, `amount`, `created_at`.
- [ ] Implement `AuditEvent` with `id`, `payment_id`, `event_type`, `payload`, `created_at`.
- [ ] Implement schema-ready `Settlement` and `SettlementPayment`.
- [ ] Run `pytest tests/test_models.py -v` and confirm pass.
- [ ] Commit: `feat: add payment domain ORM models`.

## Task 3: Pydantic Schemas

**Files:**
- Create: `app/schemas/__init__.py`
- Create: `app/schemas/payment.py`
- Create: `app/schemas/ledger_entry.py`
- Create: `app/schemas/audit_event.py`
- Test: `tests/test_schemas.py`

- [ ] Write failing schema tests for valid payment creation, invalid amounts, missing fields, and nested payment detail serialization.
- [ ] Run `pytest tests/test_schemas.py -v` and confirm imports fail.
- [ ] Implement `PaymentCreate` with `merchant_id: UUID` and `amount: int = Field(gt=0)`.
- [ ] Implement `PaymentResponse`.
- [ ] Implement `LedgerEntryResponse`.
- [ ] Implement `AuditEventResponse`.
- [ ] Implement `PaymentDetailResponse` with nested `ledger_entries` and `audit_events`.
- [ ] Run `pytest tests/test_schemas.py -v` and confirm pass.
- [ ] Commit: `feat: add payment API schemas`.

## Task 4: Repositories

**Files:**
- Create: `app/repositories/__init__.py`
- Create: `app/repositories/payment.py`
- Create: `app/repositories/ledger_entry.py`
- Create: `app/repositories/audit_event.py`
- Test: `tests/test_repositories.py`

- [ ] Write failing repository tests for create/get payment, get by idempotency key, versioned update, create/list ledger entries, and create/list audit events.
- [ ] Run `pytest tests/test_repositories.py -v` and confirm imports fail.
- [ ] Implement `PaymentRepository.create()`.
- [ ] Implement `PaymentRepository.get_by_id()`.
- [ ] Implement `PaymentRepository.get_by_idempotency()`.
- [ ] Implement `PaymentRepository.update_with_version()`.
- [ ] Implement `LedgerEntryRepository.create()` and `get_by_payment_id()`.
- [ ] Implement `AuditEventRepository.create()` and `get_by_payment_id()`.
- [ ] Run `pytest tests/test_repositories.py -v` and confirm pass.
- [ ] Commit: `feat: add payment repositories`.

## Task 5: Payment Service

**Files:**
- Create: `app/services/__init__.py`
- Create: `app/services/exceptions.py`
- Create: `app/services/payment.py`
- Test: `tests/test_payment_service.py`

- [ ] Write failing service tests for idempotent creation, idempotency conflict, successful settlement, missing payment, duplicate settlement, and rollback behavior.
- [ ] Run `pytest tests/test_payment_service.py -v` and confirm imports fail.
- [ ] Implement domain exceptions: `PaymentNotFoundError`, `IdempotencyConflictError`, `InvalidStateTransitionError`, `ConcurrencyError`.
- [ ] Implement `PaymentService.create_payment()`.
- [ ] Ensure payment creation writes exactly one `PAYMENT_CREATED` audit event.
- [ ] Ensure duplicate payment creation with same amount returns the existing payment.
- [ ] Ensure duplicate payment creation with different amount raises `IdempotencyConflictError`.
- [ ] Implement `PaymentService.settle_payment()`.
- [ ] Ensure settlement writes exactly one `LedgerEntry` and one `SETTLEMENT_SUCCEEDED` audit event.
- [ ] Ensure duplicate settlement returns the current payment without duplicate ledger rows.
- [ ] Ensure all service writes happen in a single DB transaction.
- [ ] Run `pytest tests/test_payment_service.py -v` and confirm pass.
- [ ] Commit: `feat: add payment service with idempotency and settlement`.

## Task 6: API Routes

**Files:**
- Create: `app/api/__init__.py`
- Create: `app/api/deps.py`
- Create: `app/api/health.py`
- Create: `app/api/payments.py`
- Modify: `app/main.py`
- Test: `tests/test_health.py`
- Test: `tests/test_payments_api.py`

- [ ] Write failing `GET /healthz` test.
- [ ] Write failing API tests for `POST /payments`, `GET /payments/{id}`, and `POST /payments/{id}/settle`.
- [ ] Run `pytest tests/test_health.py tests/test_payments_api.py -v` and confirm route failures.
- [ ] Implement `GET /healthz` returning `{"status": "ok"}`.
- [ ] Implement `POST /payments` with required `Idempotency-Key` header.
- [ ] Return `201` when a payment is newly created.
- [ ] Return `200` when a duplicate idempotent create returns an existing payment.
- [ ] Return `409` for idempotency key reuse with a different amount.
- [ ] Implement `GET /payments/{id}` returning payment, ledger entries, and audit events.
- [ ] Return `404` for unknown payments.
- [ ] Implement `POST /payments/{id}/settle`.
- [ ] Ensure settle returns current payment detail with ledger and audit events.
- [ ] Register routers and exception handlers in `app/main.py`.
- [ ] Run `pytest tests/test_health.py tests/test_payments_api.py -v` and confirm pass.
- [ ] Commit: `feat: add payment and health API routes`.

## Task 7: End-To-End Verification

**Files:**
- Modify only if verification reveals a bug.

- [ ] Run full test suite: `pytest -v`.
- [ ] Confirm all tests pass.
- [ ] Manually verify OpenAPI docs expose only `POST /payments`, `GET /payments/{id}`, `POST /payments/{id}/settle`, and `GET /healthz`.
- [ ] Confirm no hardcoded secrets exist.
- [ ] Confirm no route exposes settlement internals.
- [ ] Commit fixes if needed.

## Expected API Behavior

### `POST /payments`

Request:

```json
{
  "merchant_id": "00000000-0000-0000-0000-000000000001",
  "amount": 1000
}
```

Header:

```text
Idempotency-Key: merchant-generated-key
```

Response:

```json
{
  "id": "payment uuid",
  "merchant_id": "merchant uuid",
  "idempotency_key": "merchant-generated-key",
  "amount": 1000,
  "status": "pending",
  "version": 1,
  "ledger_entries": [],
  "audit_events": [
    {"event_type": "payment_created"}
  ]
}
```

### `POST /payments/{id}/settle`

Response after successful settlement:

```json
{
  "status": "settled",
  "version": 2,
  "ledger_entries": [
    {"entry_type": "debit", "amount": 1000}
  ],
  "audit_events": [
    {"event_type": "payment_created"},
    {"event_type": "settlement_succeeded"}
  ]
}
```

## Verification Commands

```bash
pytest tests/test_enums.py -v
pytest tests/test_models.py -v
pytest tests/test_schemas.py -v
pytest tests/test_repositories.py -v
pytest tests/test_payment_service.py -v
pytest tests/test_health.py tests/test_payments_api.py -v
pytest -v
```

## Risks And Checks

- SQLite does not enforce every Postgres behavior. Keep tests behavioral and avoid SQLite-specific assumptions.
- Idempotency must be enforced at both service and database constraint level.
- Ledger and audit writes must happen in the same transaction as payment state changes.
- Do not add authorize, refund, or settlement listing routes in v1.
- Do not log idempotency keys as secrets are not expected, but avoid noisy request-body logging.

## Execution Options

Plan saved to `docs/superpowers/plans/2026-05-28-payments-domain-implementation.md`.

1. Subagent-Driven (recommended): dispatch a fresh subagent per task, review between tasks.
2. Inline Execution: execute tasks in this session using executing-plans with checkpoints.
