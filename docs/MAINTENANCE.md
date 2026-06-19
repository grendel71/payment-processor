# Maintenance Guide

Operational reference for the Payment Processor v1. Covers local run,
test, deployment, common ops tasks, schema evolution, and
troubleshooting. Authored against `main` at commit `90aa322`.

For API contracts see `docs/API_REFERENCE.md`. For design rationale see
`docs/superpowers/specs/2026-05-28-payments-domain-design.md`.

---

## 1. Local development

### 1.1 Environment

The Nix flake (`flake.nix`) provisions Python 3.12, kubectl, helm,
terraform, awscli2, eksctl, and direnv. On `nix develop` (or direnv
auto-load via `.envrc` → `use flake`), the shellHook creates `.venv`
and installs `requirements.txt` (falls back to fastapi/uvicorn/pydantic
if absent).

```bash
# Enter the dev shell (or rely on direnv)
nix develop

# Or, outside Nix, create a venv manually
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
```

`requirements.txt` — runtime deps: sqlalchemy, fastapi, psycopg2-binary,
alembic, uvicorn, pydantic.

`requirements-dev.txt` — adds pytest, httpx (for FastAPI TestClient).

### 1.2 Run the API locally

```bash
.venv/bin/uvicorn app.main:app --reload --port 8000
```

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
- Health: http://localhost:8000/healthz

On startup, `app/main.py` lifespan calls `Base.metadata.create_all`,
creating `paymentprocessor.db` (SQLite) in the working directory if
missing. **Do not commit `*.db` files** — they are gitignored.

### 1.3 Run tests

```bash
.venv/bin/pytest                  # full suite (58 tests)
.venv/bin/pytest -v               # verbose
.venv/bin/pytest tests/test_payments_api.py -v   # one file
```

Tests use an in-memory SQLite per test via the `isolated_db` fixture in
`tests/conftest.py` (`StaticPool` shares one connection across the
lifespan's `create_all` and request sessions). No external services
required.

---

## 2. Layered architecture (maintenance view)

```
app/api/        Thin routes: parse → service → commit → serialize
app/services/   Domain rules: idempotency, state machine, ledger, audit
app/repositories/  SQLAlchemy access; optimistic-concurrency guard
app/models/     ORM models + enums
app/schemas/    Pydantic v2 request/response DTOs
app/db.py       Engine, SessionLocal, Base
app/main.py     FastAPI factory, lifespan, exception handlers
```

**Invariant: the service does not commit.** The API route owns
`session.commit()`. The `get_db` dependency rolls back on any
exception. Ledger and audit inserts share the payment state-transition
transaction — if any of the three fails, none persist.

**Invariant: ledger and audit tables are append-only.** No application
code path issues UPDATE or DELETE against `ledger_entries` or
`audit_events`. Enforced by contract in the repository layer; a
production hardening step (see §6) is to add DB-level triggers or
GRANTs.

---

## 3. Database

### 3.1 Backend

Default: SQLite (`sqlite:///./paymentprocessor.db`) for local/test.
Production target: Postgres. The models use SQLAlchemy's portable
`Uuid` and `JSON` types so the same code runs on both — **do not**
switch back to `sqlalchemy.types.UUID` (emits literal `UUID` affinity
on SQLite, which silently coerces all-digit UUID strings to integers
and breaks re-reads; see §7 troubleshooting).

### 3.2 Schema creation

`Base.metadata.create_all(bind=engine)` runs in the app lifespan. This
is acceptable for dev/test. **For production, use Alembic migrations**
(not yet wired up — see §6).

### 3.3 Tables

| Table | Purpose | Mutable? |
|---|---|---|
| `payments` | aggregate root | UPDATE only via `update_with_version` |
| `ledger_entries` | immutable debit records | INSERT only |
| `audit_events` | immutable state-transition log | INSERT only |
| `settlements` | schema-ready, unused by v1 API | — |
| `settlement_payments` | join, schema-ready | — |

### 3.4 Critical constraints

- `uq_merchant_idempotency` on `payments(merchant_id, idempotency_key)`
  — the DB-level idempotency guarantee. Never drop.
- `uq_settlement_payment_payment` on `settlement_payments(payment_id)`
  — a payment belongs to at most one settlement.
- FKs `ledger_entries.payment_id` and `audit_events.payment_id` use
  `ondelete=RESTRICT` — never cascade-delete a payment with children.

---

## 4. Operational tasks

### 4.1 Replay a stuck payment

A payment stuck in `pending` after a settle attempt that the client
believes succeeded can be safely re-settled: `POST /payments/{id}/settle`
is idempotent. If the payment is already `settled`, the route returns
200 with no new writes. If still `pending`, settlement proceeds.

### 4.2 Investigate a payment

```sql
SELECT id, status, version, amount, created_at, updated_at
  FROM payments WHERE id = ?;

SELECT event_type, payload, created_at
  FROM audit_events WHERE payment_id = ? ORDER BY created_at;

SELECT entry_type, amount, created_at
  FROM ledger_entries WHERE payment_id = ? ORDER BY created_at;
```

The `audit_events.payload` JSON contains `previous_status`,
`new_status`, and `requested_amount` for each transition.

### 4.3 Detect duplicate idempotency-key misuse

A `409` on `POST /payments` with body
`{"detail": "idempotency key '...' already used with different payload"}`
means a client reused a key with a different amount. Investigate the
client — this is almost always a bug in their idempotency-key
generation, not a server fault.

### 4.4 Concurrency conflicts

A `409` with `concurrent modification of payment {id}; retry` means the
optimistic-version guard fired: two writers tried to settle the same
payment simultaneously. The client should retry. If persistent, check
for duplicate workers or retry storms.

---

## 5. Deployment

### 5.1 Container

Not yet implemented. Production deployment should:
- Build a non-root container image (per AGENTS.md Kubernetes standards).
- Set `DATABASE_URL` to the Postgres DSN via a Kubernetes Secret —
  never bake it into the image.
- Run `uvicorn app.main:app --host 0.0.0.0 --port 8000` as the entrypoint.

### 5.2 Kubernetes

Helm chart structure (per `.opencode/instructions/helm.md`) should
include Deployment, Service, Ingress, ConfigMap, Secret, HPA. Required:
readiness probe on `GET /healthz`, liveness probe on `GET /healthz`,
resource requests/limits, non-root container, namespace isolation.

### 5.3 Configuration

The only runtime config today is `DATABASE_URL` in `app/db.py:19`,
hardcoded to SQLite. **Before any production deployment, externalize
this** to read from an environment variable:

```python
import os
DATABASE_URL = os.environ["DATABASE_URL"]
```

This is a known gap — see §6.

---

## 6. Known gaps & hardening backlog

Items explicitly out of v1 scope per the design spec, ordered by
production-readiness priority:

1. **Externalize `DATABASE_URL`** — currently hardcoded in `app/db.py`.
   Read from env var. Trivial change, blocking production.
2. **Alembic migrations** — `create_all` is dev-only. Add `alembic/`
   with an initial migration matching the current schema.
3. **Postgres backend** — switch the production engine to Postgres for
   native UUID/JSON, row-level locking, and proper unique-constraint
   error semantics.
4. **Merchant model** — `merchant_id` is an unvalidated UUID. Add a
   `merchants` table and FK when the merchant lifecycle is defined.
5. **Append-only enforcement at DB level** — add `REVOKE UPDATE, DELETE
   ON ledger_entries, audit_events` from the app role, or triggers that
   raise on UPDATE/DELETE. Currently enforced only by code contract.
6. **Currency column** — `amount` is int cents with no currency. Add a
   `currency` column (default `'USD'`) before multi-currency support.
7. **Refund flow** — entity model supports adding `original_payment_id`
   FK later; no v1 endpoint.
8. **Settlement API** — `Settlement`/`SettlementPayment` tables exist
   but no route reads/writes them. Add when batch reconciliation is
   needed.
9. **`SETTLEMENT_FAILED` path** — the service writes
   `SETTLEMENT_SUCCEEDED` only. The `failed` status and
   `SETTLEMENT_FAILED` audit type exist but no v1 route transitions a
   payment to `failed`. Wire up when a real settlement failure mode is
   defined.
10. **Logging** — avoid logging request bodies (may contain PII). No
    structured logging is wired up yet.

---

## 7. Troubleshooting

### 7.1 `'int' object has no attribute 'replace'` on UUID re-read

**Symptom:** `AttributeError` from `uuid.py` when reading a payment
back whose `merchant_id` is all digits (e.g. the test fixture
`00000000-0000-0000-0000-000000000001`).

**Cause:** The model uses `sqlalchemy.types.UUID` (capital), which
emits a column with literal type name `UUID` on SQLite. SQLite gives
this NUMERIC affinity, so all-digit UUID strings are coerced to
integers on storage. The result processor then calls `UUID(int_value)`
with a non-string and crashes.

**Fix:** Use the portable `sqlalchemy.Uuid` (lowercase), which emits
`CHAR(32)` → TEXT affinity. This is what the current code does. If you
see this error again, someone reverted to `sqlalchemy.types.UUID` —
revert them.

### 7.2 `DetachedInstanceError` after commit

**Symptom:** `Instance <Payment> is not bound to a Session` when
serializing a response after `session.commit()`.

**Cause:** SQLAlchemy's default `expire_on_commit=True` expires all
attributes after commit; accessing them on a now-detached instance
triggers a lazy refresh that fails.

**Fix:** `SessionLocal` is configured with `expire_on_commit=False`.
Do not re-enable it. The tradeoff: post-commit attribute access reads
cached values rather than re-querying. This is safe because the service
is the single writer within a request and the values are authoritative
immediately after commit. The API routes additionally re-read via
`get_payment_detail` after commit to refresh relationship state.

### 7.3 In-memory SQLite tests see "no such table"

**Symptom:** `sqlite3.OperationalError: no such table: payments` in
tests despite `Base.metadata.create_all` in the lifespan.

**Cause:** `sqlite:///:memory:` gives each connection a separate empty
database. With SQLAlchemy's default pool, the lifespan's `create_all`
and the request session see different in-memory DBs.

**Fix:** The `isolated_db` fixture in `tests/conftest.py` uses
`poolclass=StaticPool` to share one connection. Do not remove
`StaticPool` from the test engine config.

### 7.4 Stale relationship after settle (empty `ledger_entries` in response)

**Symptom:** `POST /payments/{id}/settle` returns 200 with
`status: settled` but `ledger_entries: []`.

**Cause:** The `Payment.ledger_entries` relationship is
`selectin`-loaded. After the service inserts a `LedgerEntry` via the
repository (FK-only, not appended to the relationship collection), the
in-memory `payment.ledger_entries` list is stale.

**Fix:** The route re-reads via `svc.get_payment_detail(payment_id)`
after commit. If you refactor the route to return the service's return
value directly, you will reintroduce this bug.

### 7.5 `Idempotency-Key` header rejected with 422

**Symptom:** `POST /payments` returns 422 even though the header looks
present.

**Cause:** The dependency `get_idempotency_key` requires the header
with alias `Idempotency-Key` (capital I, capital K), `min_length=1`,
`max_length=64`. Empty values, values longer than 64 chars, or the
wrong casing will be rejected.

**Fix:** Send exactly `Idempotency-Key: <value>` with 1–64 characters.

---

## 8. Test inventory

58 tests across 7 files. Run `pytest --collect-only -q` for the full
list. Coverage by layer:

| File | Layer | Count | What it covers |
|---|---|---|---|
| `tests/test_enums.py` | domain | 5 | enum values, str-Enum base, db module exports |
| `tests/test_models.py` | ORM | 10 | table creation, columns, unique constraint, FKs, JSON payload, defaults |
| `tests/test_schemas.py` | pydantic | 10 | amount validation, required fields, nested serialization |
| `tests/test_repositories.py` | data access | 9 | CRUD, idempotency lookup, versioned update, stale-version guard |
| `tests/test_payment_service.py` | service | 10 | idempotent create, conflict, settle, rollback, state guards, audit payload |
| `tests/test_health.py` | API | 2 | healthz, OpenAPI route surface |
| `tests/test_payments_api.py` | API | 12 | all 4 routes end-to-end, status codes, idempotency, validation |

When adding a feature, add tests at the lowest layer that can verify
the behavior (service for domain rules, API for HTTP contracts). Avoid
duplicating the same assertion across layers.

---

## 9. Commit & branch conventions

This repository was built using the superpowers workflow: design spec
→ implementation plan → TDD per task → merge. The implementation
landed as a single `feat:` commit (`15e65e6`) merged via `--no-ff`
into `main` (`90aa322`). Future work should follow the same pattern:

1. Brainstorm → write spec to `docs/superpowers/specs/`.
2. Write plan to `docs/superpowers/plans/`.
3. Branch from `main`, implement task-by-task with TDD.
4. Verify full suite passes before merge.
5. `--no-ff` merge into `main` with a descriptive merge commit.

Commit message prefix convention: `feat:`, `fix:`, `docs:`, `chore:`,
`refactor:`.
