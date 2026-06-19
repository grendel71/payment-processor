# API Reference

Reference for the Payment Processor v1 API and the internal service,
repository, and schema contracts that back it. Authored from the code
in `app/` at commit `90aa322` (post-merge of `feature/payments-domain`).

For design rationale and failure-scenario analysis see
`docs/superpowers/specs/2026-05-28-payments-domain-design.md`.

---

## 1. HTTP API

Base URL: served by the FastAPI app constructed in `app/main.py:create_app()`.
Interactive docs available at `/docs` (Swagger) and `/redoc` (ReDoc) when
the app runs with default FastAPI config.

All routes are registered via routers in `app/api/`. Routes are thin:
they parse the request, call `PaymentService`, commit the session, and
serialize. Domain rules live in the service layer.

### 1.1 `GET /healthz`

Liveness probe. No dependencies touched.

| | |
|---|---|
| File | `app/api/health.py:9` |
| Auth | none |
| Idempotency | n/a |

**Response** `200 OK`

```json
{"status": "ok"}
```

### 1.2 `POST /payments`

Create a new payment in `pending` status, or replay an existing
idempotent request. On a fresh create, writes one `Payment` row and one
`PAYMENT_CREATED` audit event in a single transaction.

| | |
|---|---|
| File | `app/api/payments.py:18` |
| Auth | none |
| Idempotency | **required** — `Idempotency-Key` header |

**Request headers**

| Header | Required | Constraints |
|---|---|---|
| `Idempotency-Key` | yes | 1–64 chars; unique per `merchant_id` |

**Request body** — `PaymentCreate` (`app/schemas/payment.py:18`)

| Field | Type | Constraint |
|---|---|---|
| `merchant_id` | UUID | required |
| `amount` | int | `> 0` (minor units / cents) |

**Responses**

| Status | When | Body |
|---|---|---|
| `201 Created` | new payment created | `PaymentDetailResponse` |
| `200 OK` | idempotent replay (same merchant+key+amount) | `PaymentDetailResponse` (existing) |
| `409 Conflict` | key reused with a different `amount` | `{"detail": "..."}` |
| `422 Unprocessable Entity` | missing header, missing field, or `amount <= 0` | FastAPI validation error |

**Example**

```http
POST /payments
Idempotency-Key: order-1234
Content-Type: application/json

{"merchant_id": "00000000-0000-0000-0000-000000000001", "amount": 2500}
```

```json
{
  "id": "7bcef3e3-...",
  "merchant_id": "00000000-0000-0000-0000-000000000001",
  "idempotency_key": "order-1234",
  "amount": 2500,
  "status": "pending",
  "version": 1,
  "created_at": "2026-06-19T02:51:49.027182",
  "updated_at": "2026-06-19T02:51:49.027197",
  "ledger_entries": [],
  "audit_events": [
    {"id": "...", "payment_id": "...", "event_type": "payment_created",
     "payload": {"previous_status": null, "new_status": "pending", "requested_amount": 2500},
     "created_at": "..."}
  ]
}
```

### 1.3 `GET /payments/{payment_id}`

Retrieve a payment with its ledger entries and audit events.

| | |
|---|---|
| File | `app/api/payments.py:40` |
| Auth | none |

**Path parameter**

| Name | Type |
|---|---|
| `payment_id` | UUID |

**Responses**

| Status | When | Body |
|---|---|---|
| `200 OK` | found | `PaymentDetailResponse` |
| `404 Not Found` | no payment with that id | `{"detail": "payment {id} not found"}` |

### 1.4 `POST /payments/{payment_id}/settle`

Transition a `pending` payment to `settled`. Writes one `LedgerEntry`
(DEBIT) and one `SETTLEMENT_SUCCEEDED` audit event in the same
transaction as the status change. Idempotent on already-settled
payments: returns current state, writes nothing.

| | |
|---|---|
| File | `app/api/payments.py:46` |
| Auth | none |
| Idempotency | **required** — `Idempotency-Key` header |

**Path parameter**

| Name | Type |
|---|---|
| `payment_id` | UUID |

**Request body** — none (the `Idempotency-Key` header carries the
idempotency contract).

**Responses**

| Status | When | Body |
|---|---|---|
| `200 OK` | settlement succeeded OR replay of an already-settled payment | `PaymentDetailResponse` |
| `404 Not Found` | no payment with that id | `{"detail": "..."}` |
| `409 Conflict` | payment is `failed` (invalid state transition) or concurrent modification detected | `{"detail": "..."}` |
| `422 Unprocessable Entity` | missing `Idempotency-Key` header | FastAPI validation error |

**State machine enforced**

```
pending ──settle✓──→ settled    (200, ledger + audit written)
pending ──settle✗──→ failed     (v1: not exposed via this route)
settled ──settle──→ settled      (200, idempotent replay, no writes)
failed  ──settle──→ 409          (InvalidStateTransitionError)
```

---

## 2. Schemas (Pydantic v2)

All in `app/schemas/`. Each response schema uses
`ConfigDict(from_attributes=True)` so it can be built directly from an
ORM instance.

### `PaymentCreate` — `app/schemas/payment.py:16`
| Field | Type | Validation |
|---|---|---|
| `merchant_id` | `UUID` | required |
| `amount` | `int` | `gt=0` |

### `SettleRequest` — `app/schemas/payment.py:21`
Empty body. Idempotency key arrives via the `Idempotency-Key` header.

### `PaymentResponse` — `app/schemas/payment.py:25`
| Field | Type |
|---|---|
| `id` | `UUID` |
| `merchant_id` | `UUID` |
| `idempotency_key` | `str` |
| `amount` | `int` |
| `status` | `str` (one of `pending`, `settled`, `failed`) |
| `version` | `int` |
| `created_at` | `datetime` |
| `updated_at` | `datetime` |

### `LedgerEntryResponse` — `app/schemas/ledger_entry.py:8`
| Field | Type |
|---|---|
| `id` | `UUID` |
| `payment_id` | `UUID` |
| `entry_type` | `str` (`debit`) |
| `amount` | `int` |
| `created_at` | `datetime` |

### `AuditEventResponse` — `app/schemas/audit_event.py:9`
| Field | Type |
|---|---|
| `id` | `UUID` |
| `payment_id` | `UUID` |
| `event_type` | `str` (see `AuditEventType`) |
| `payload` | `dict[str, Any]` |
| `created_at` | `datetime` |

### `PaymentDetailResponse` — `app/schemas/payment.py:38`
Extends `PaymentResponse` with:
| Field | Type | Default |
|---|---|---|
| `ledger_entries` | `list[LedgerEntryResponse]` | `[]` |
| `audit_events` | `list[AuditEventResponse]` | `[]` |

---

## 3. Service layer — `app/services/payment.py`

`PaymentService` owns idempotency, the state machine, ledger writes,
audit writes, and transaction scoping. It **does not commit**; the
caller (the API route) owns `session.commit()`. Any exception bubbles up
and the FastAPI `get_db` dependency rolls back.

Constructor: `PaymentService(session: Session)` — instantiates
`PaymentRepository`, `LedgerEntryRepository`, `AuditEventRepository`
against the same session.

### `create_payment(*, merchant_id, idempotency_key, amount) -> tuple[Payment, bool]`
`app/services/payment.py:43`

1. Look up existing payment by `(merchant_id, idempotency_key)`.
2. If found:
   - amount matches → return `(existing, False)` (idempotent replay, no writes).
   - amount differs → raise `IdempotencyConflictError`.
3. Otherwise insert a new `Payment` (status=`pending`, version=1) and one
   `PAYMENT_CREATED` audit event with payload
   `{"previous_status": None, "new_status": "pending", "requested_amount": amount}`.
4. Return `(payment, True)`.

### `settle_payment(payment_id) -> Payment`
`app/services/payment.py:82`

1. Load payment; raise `PaymentNotFoundError` if missing.
2. If already `settled` → return current payment (idempotent, no writes).
3. If `failed` → raise `InvalidStateTransitionError`.
4. If status not in `{pending}` → raise `InvalidStateTransitionError`.
5. Optimistic-concurrency UPDATE: `version` filter; on zero rows matched
   raise `ConcurrencyError` (caller may retry).
6. Insert one `LedgerEntry` (DEBIT, `amount == payment.amount`).
7. Insert one `SETTLEMENT_SUCCEEDED` audit event with payload
   `{"previous_status": "pending", "new_status": "settled", "requested_amount": amount}`.
8. Return updated payment.

### `get_payment_detail(payment_id) -> Payment`
`app/services/payment.py:127`

Load payment via repository (which eager-loads `ledger_entries` and
`audit_events` via `selectin` relationships). Raise
`PaymentNotFoundError` if missing.

### Domain exceptions — `app/services/exceptions.py`

| Exception | Raised when | HTTP |
|---|---|---|
| `PaymentNotFoundError` | payment id not found | 404 |
| `IdempotencyConflictError` | idempotency key reused with different amount | 409 |
| `InvalidStateTransitionError` | settle on a `failed` payment | 409 |
| `ConcurrencyError` | optimistic-version check failed (concurrent write) | 409 |

Mapped in `app/main.py` via `@app.exception_handler(...)`.

---

## 4. Repository layer

Each repository takes a `Session` and isolates SQLAlchemy access. Child
repositories (`LedgerEntryRepository`, `AuditEventRepository`) generate
their own UUIDs internally; the service owns the aggregate root's id.

### `PaymentRepository` — `app/repositories/payment.py`

| Method | Signature | Notes |
|---|---|---|
| `create` | `(*, id, merchant_id, idempotency_key, amount) -> Payment` | status=`pending`, version=1; `flush()` exposes the row |
| `get_by_id` | `(payment_id) -> Payment \| None` | `session.get` |
| `get_by_idempotency` | `(merchant_id, idempotency_key) -> Payment \| None` | query by unique pair |
| `update_with_version` | `(payment, *, status) -> Payment` | UPDATE WHERE `id AND version`; raises `StaleVersionError` if 0 rows |

### `LedgerEntryRepository` — `app/repositories/ledger_entry.py`

| Method | Signature | Notes |
|---|---|---|
| `create` | `(*, payment_id, entry_type, amount) -> LedgerEntry` | INSERT-only by contract |
| `get_by_payment_id` | `(payment_id) -> list[LedgerEntry]` | ordered by `created_at` |

### `AuditEventRepository` — `app/repositories/audit_event.py`

| Method | Signature | Notes |
|---|---|---|
| `create` | `(*, payment_id, event_type, payload) -> AuditEvent` | INSERT-only by contract |
| `get_by_payment_id` | `(payment_id) -> list[AuditEvent]` | ordered by `created_at` |

### `StaleVersionError` — `app/repositories/exceptions.py`
Raised by `update_with_version` when the optimistic-concurrency guard
fires. The service translates this to `ConcurrencyError` for callers.

---

## 5. ORM models — `app/models/`

All inherit from `app.db.Base` (SQLAlchemy 2.0 `DeclarativeBase`).

### `Payment` — `app/models/payment.py`
Aggregate root. `__table_args__` defines
`UniqueConstraint("merchant_id", "idempotency_key", name="uq_merchant_idempotency")`.

| Column | Type | Notes |
|---|---|---|
| `id` | `Uuid(as_uuid=True)` | PK |
| `merchant_id` | `Uuid(as_uuid=True)` | indexed |
| `idempotency_key` | `String(64)` | |
| `amount` | `Integer` | minor units |
| `status` | `Enum(PaymentStatus, values_callable=...)` | stores `pending`/`settled`/`failed` |
| `version` | `Integer` | optimistic-concurrency token |
| `created_at` | `DateTime` | UTC, default now |
| `updated_at` | `DateTime` | UTC, default + onupdate now |

Relationships (`selectin` lazy): `ledger_entries`, `audit_events`.

### `LedgerEntry` — `app/models/ledger_entry.py`
Append-only. FK `payment_id → payments.id` (`ondelete=RESTRICT`).

| Column | Type |
|---|---|
| `id` | `Uuid` PK |
| `payment_id` | `Uuid` FK, indexed |
| `entry_type` | `Enum(LedgerEntryType)` (`debit`) |
| `amount` | `Integer` |
| `created_at` | `DateTime` |

### `AuditEvent` — `app/models/audit_event.py`
Append-only. FK `payment_id → payments.id` (`ondelete=RESTRICT`).

| Column | Type |
|---|---|
| `id` | `Uuid` PK |
| `payment_id` | `Uuid` FK, indexed |
| `event_type` | `Enum(AuditEventType)` |
| `payload` | `JSON` |
| `created_at` | `DateTime` |

### `Settlement`, `SettlementPayment` — `app/models/settlement.py`
Schema-ready only. Tables exist for future batch reconciliation; **no
v1 API route reads or writes them.**

### Enums — `app/models/enums.py`

| Enum | Values |
|---|---|
| `PaymentStatus` | `pending`, `settled`, `failed` |
| `LedgerEntryType` | `debit` |
| `AuditEventType` | `payment_created`, `settlement_initiated`, `settlement_succeeded`, `settlement_failed` |

---

## 6. Configuration & dependencies — `app/db.py`, `app/api/deps.py`

### `app/db.py`
- `DATABASE_URL` defaults to `sqlite:///./paymentprocessor.db`.
- `engine` — `create_engine(DATABASE_URL, check_same_thread=False, future=True)`.
- `SessionLocal` — `sessionmaker(..., expire_on_commit=False)`. The
  `expire_on_commit=False` is intentional: API routes serialize ORM
  objects after commit; without this they would detach.
- `Base` — declarative base for all models.
- `get_db()` — module-level dependency (also defined here for direct
  use). The `app/api/deps.py` version is identical but reads
  `db_module.SessionLocal` lazily so test fixtures can rebind it.

### `app/api/deps.py`
- `get_db()` — yields a session, rolls back on exception, closes.
  Reads `app.db.SessionLocal` at call time (not import time) so test
  fixtures that rebind the module attribute take effect.
- `get_idempotency_key()` — FastAPI `Header(..., alias="Idempotency-Key",
  min_length=1, max_length=64)`. Returns 422 if absent.
- `DbDep`, `IdempotencyKeyDep` — `Depends(...)` aliases for reuse.

### `app/main.py`
- `create_app()` — factory; registers `health_router` and
  `payments_router`, installs `lifespan` that calls
  `Base.metadata.create_all(bind=db_module.engine)` on startup, and
  registers exception handlers mapping service exceptions to HTTP 404/409.
- Module-level `app = create_app()` for `uvicorn app.main:app`.
