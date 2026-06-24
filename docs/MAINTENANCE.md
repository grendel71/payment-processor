# Maintenance Guide

Operational reference for the Payment Processor v1. Covers local run,
test, deployment, common ops tasks, schema evolution, and
troubleshooting. Authored after the PostgreSQL/Alembic refactor.

For API contracts see `docs/API_REFERENCE.md`. For design rationale see:
- `docs/superpowers/specs/2026-05-28-payments-domain-design.md`
- `docs/superpowers/specs/2026-06-19-postgresql-refactor-design.md`

---

## 1. Local development

### 1.1 Environment

The Nix flake (`flake.nix`) provisions Python 3.12, kubectl, helm,
terraform, awscli2, eksctl, and direnv. On `nix develop` (or direnv
auto-load via `.envrc` -> `use flake`), the shellHook creates `.venv`
and installs `requirements.txt` (falls back to fastapi/uvicorn/pydantic
if absent).

```bash
# Enter the dev shell (or rely on direnv)
nix develop

# Or, outside Nix, create a venv manually
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
```

`.env.example` ships local sample values only. Copy it to `.env` for
host-based development; `.env` is gitignored and must not contain real
production secrets.

```bash
test -f .env || cp .env.example .env
```

`requirements.txt` runtime deps: sqlalchemy, fastapi, psycopg2-binary,
alembic, uvicorn, pydantic.

`requirements-dev.txt` adds pytest and httpx for FastAPI TestClient.

### 1.2 Run the API locally

Run against the dockerized Postgres service from the host:

```bash
# 1. Spin up Postgres
docker compose up -d db

# 2. Apply schema
set -a; . ./.env; set +a   # optional if using .env
.venv/bin/alembic upgrade head

# 3. Run the API from the host
.venv/bin/uvicorn app.main:app --reload --port 8000
```

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
- Health: http://localhost:8000/healthz

Run the fully containerized stack:

```bash
docker compose up --build
# API at http://localhost:8000
# Schema still needs `alembic upgrade head` run against compose-managed PG,
# or run it as an init/deploy step in production.
```

### 1.3 Run tests

```bash
docker compose up -d db
.venv/bin/pytest -v
.venv/bin/pytest tests/test_alembic.py -v
.venv/bin/pytest tests/test_payments_api.py -v
```

Tests use a separate `paymentprocessor_test` database created
automatically by `tests/conftest.py`. The session-scoped `engine`
fixture runs `alembic upgrade head` once per test session. The autouse
`truncate_tables` fixture uses `TRUNCATE ... RESTART IDENTITY CASCADE`
between tests while preserving Alembic's version table so migration state
remains valid. The current suite has 72 tests.

---

## 2. Layered architecture (maintenance view)

```
app/api/            Thin routes: parse -> service -> commit -> serialize
app/services/       Domain rules: idempotency, state machine, ledger, audit
app/repositories/   SQLAlchemy access; optimistic-concurrency guard
app/models/         ORM models + enums
app/schemas/        Pydantic v2 request/response DTOs
app/db.py           Engine, SessionLocal, Base
app/main.py         FastAPI factory, no-op lifespan, exception handlers
migrations/         Alembic schema migrations
```

**Invariant: the service does not commit.** The API route owns
`session.commit()`. The `get_db` dependency rolls back on any exception.
Ledger and audit inserts share the payment state-transition transaction
-- if any of the three fails, none persist.

**Invariant: ledger and audit tables are append-only.** No application
code path issues UPDATE or DELETE against `ledger_entries` or
`audit_events`. Enforced by contract in the repository layer; a
production hardening step (see Section 6) is to add DB-level triggers or
GRANTs.

---

## 3. Database

### 3.1 Backend

PostgreSQL 16 is the backend. Local development and tests use the
`postgres:16-alpine` service defined in `docker-compose.yml`.

`app.db._build_dsn()` reads `DATABASE_URL` if set; otherwise it composes
a Postgres DSN from `POSTGRES_*` variables. Dev-safe defaults match
`.env.example`: `pp`, `pp`, `paymentprocessor`, `localhost`, `5432`.
Bare `postgres://` URLs are normalized to `postgresql://` for Heroku /
Render compatibility. `pool_pre_ping=True` guards against stale
containerized connections.

| Var | Default | Notes |
|---|---|---|
| `DATABASE_URL` | unset | Optional override; wins if set |
| `POSTGRES_USER` | `pp` | Dev default only; set explicitly in prod |
| `POSTGRES_PASSWORD` | `pp` | Dev default only; inject from secret in prod |
| `POSTGRES_DB` | `paymentprocessor` | Dev DB name |
| `POSTGRES_HOST` | `localhost` | Use `db` inside Compose |
| `POSTGRES_PORT` | `5432` | Host-mapped port |
| `POSTGRES_TEST_DB` | `paymentprocessor_test` | Test DB created by pytest fixture |

### 3.2 Schema creation / Alembic workflow

`alembic upgrade head` is the only schema-creation path. The FastAPI
lifespan does not create tables. Tests run `alembic upgrade head` once
per session through the `engine` fixture.

Developer workflow:

```bash
docker compose up -d db
.venv/bin/alembic upgrade head
.venv/bin/alembic revision --autogenerate -m "describe change"
.venv/bin/alembic downgrade -1
```

### 3.3 Tables

| Table | Purpose | Mutable? |
|---|---|---|
| `payments` | aggregate root | UPDATE only via `update_with_version` |
| `ledger_entries` | immutable debit records | INSERT only |
| `audit_events` | immutable state-transition log | INSERT only |
| `settlements` | schema-ready, unused by v1 API | -- |
| `settlement_payments` | join, schema-ready | -- |

### 3.4 Critical constraints

- `uq_merchant_idempotency` on `payments(merchant_id, idempotency_key)`
  -- the DB-level idempotency guarantee. Never drop.
- `uq_settlement_payment_payment` on `settlement_payments(payment_id)`
  -- a payment belongs to at most one settlement.
- FKs `ledger_entries.payment_id` and `audit_events.payment_id` use
  `ondelete=RESTRICT` -- never cascade-delete a payment with children.
- `settlement_payments.settlement_id` uses `ondelete=CASCADE` so deleting
  a settlement cleans up join rows.

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
  FROM payments WHERE id = '<payment-uuid>';

SELECT event_type, payload, created_at
  FROM audit_events WHERE payment_id = '<payment-uuid>' ORDER BY created_at;

SELECT entry_type, amount, created_at
  FROM ledger_entries WHERE payment_id = '<payment-uuid>' ORDER BY created_at;
```

The `audit_events.payload` JSONB contains `previous_status`,
`new_status`, and `requested_amount` for each transition.

### 4.3 Detect duplicate idempotency-key misuse

A `409` on `POST /payments` with body
`{"detail": "idempotency key '...' already used with different payload"}`
means a client reused a key with a different amount. Investigate the
client -- this is almost always a bug in their idempotency-key
generation, not a server fault.

### 4.4 Concurrency conflicts

A `409` with `concurrent modification of payment {id}; retry` means the
optimistic-version guard fired: two writers tried to settle the same
payment simultaneously. The client should retry. If persistent, check
for duplicate workers or retry storms.

---

## 5. Deployment

### 5.1 Container

The repository includes a multi-stage Dockerfile. The builder installs
dependencies; the runtime image runs as a non-root `app` user. Runtime
image contents include `app/`, `alembic.ini`, and `migrations/`.
`.dockerignore` excludes development-only files such as `.venv`, `.git`,
tests, and docs.

`docker-compose.yml` defines:
- `db`: `postgres:16-alpine`, healthchecked with `pg_isready`.
- `app`: builds the API image, exposes port 8000, and depends on healthy
  `db`.

Runtime configuration comes from environment variables. Real deployments
must inject credentials via Kubernetes Secret or a secret manager; never
ship production secrets in `.env`.

Migrations run out-of-band as a deploy step, init container, or one-shot
job. They do not run at app boot.

### 5.2 Kubernetes

Helm chart structure (per `.opencode/instructions/helm.md`) should
include Deployment, Service, Ingress, ConfigMap, Secret, HPA. Required:
readiness probe on `GET /healthz`, liveness probe on `GET /healthz`,
resource requests/limits, non-root container, namespace isolation.

### 5.3 Configuration

Set runtime configuration via environment variables. Inside Compose,
`POSTGRES_HOST=db`; from the host, `POSTGRES_HOST=localhost` with the
mapped port. In production, prefer `DATABASE_URL` injected from a secret
or inject all `POSTGRES_*` components separately.

---

## 6. Known gaps & hardening backlog

Items explicitly out of v1 scope per the design spec, ordered by
production-readiness priority:

1. **Merchant model** -- `merchant_id` is an unvalidated UUID. Add a
   `merchants` table and FK when the merchant lifecycle is defined.
2. **Append-only enforcement at DB level** -- add `REVOKE UPDATE, DELETE
   ON ledger_entries, audit_events` from the app role, or triggers that
   raise on UPDATE/DELETE. Currently enforced only by code contract.
3. **Currency column** -- `amount` is int cents with no currency. Add a
   `currency` column (default `'USD'`) before multi-currency support.
4. **Refund flow** -- entity model supports adding `original_payment_id`
   FK later; no v1 endpoint.
5. **Settlement API** -- `Settlement`/`SettlementPayment` tables exist
   but no route reads/writes them. Add when batch reconciliation is
   needed.
6. **`SETTLEMENT_FAILED` path** -- the service writes
   `SETTLEMENT_SUCCEEDED` only. The `failed` status and
   `SETTLEMENT_FAILED` audit type exist but no v1 route transitions a
   payment to `failed`. Wire up when a real settlement failure mode is
   defined.
7. **Logging and observability** -- avoid logging request bodies (may
   contain PII). Structured logs, metrics, tracing, and alerting are not
   wired up yet.
8. **Production infrastructure** -- Helm/Kubernetes/Terraform artifacts
   still need production-ready implementation: probes, resources,
   namespace isolation, secrets, remote state, encryption, and least
   privilege IAM.

---

## 7. Troubleshooting

### 7.1 Tests fail with "Postgres not available"

**Symptom:** pytest raises `RuntimeError: Postgres not available at ...`.

**Cause:** The dockerized Postgres service is not running or is not
healthy.

**Fix:**

```bash
docker compose up -d db
docker compose ps
.venv/bin/pytest -v
```

### 7.2 Alembic connects to the wrong database

**Symptom:** migrations create tables in an unexpected database, fail
with auth errors, or do not affect the database your app/tests use.

**Cause:** `DATABASE_URL` wins over `POSTGRES_*` variables.

**Fix:** Inspect and clear/set env vars deliberately:

```bash
env | grep -E 'DATABASE_URL|POSTGRES_'
unset DATABASE_URL  # if you intended POSTGRES_* composition
set -a; . ./.env; set +a
.venv/bin/alembic upgrade head
```

### 7.3 Historical: UUID re-read failure on the retired SQLite path

The old test stack used SQLite and could fail with
`'int' object has no attribute 'replace'` when a UUID column used the
wrong SQLAlchemy type. This is obsolete for current runtime/tests because
Postgres stores UUID columns natively. If a future local-only test path
reintroduces SQLite, keep using `sqlalchemy.Uuid` rather than
`sqlalchemy.types.UUID`.

### 7.4 `DetachedInstanceError` after commit

**Symptom:** `Instance <Payment> is not bound to a Session` when
serializing a response after `session.commit()`.

**Cause:** SQLAlchemy's default `expire_on_commit=True` expires all
attributes after commit; accessing them on a now-detached instance
triggers a lazy refresh that fails.

**Fix:** `SessionLocal` is configured with `expire_on_commit=False`.
Do not re-enable it. The API routes additionally re-read via
`get_payment_detail` after commit to refresh relationship state.

### 7.5 Stale relationship after settle (empty `ledger_entries` in response)

**Symptom:** `POST /payments/{id}/settle` returns 200 with
`status: settled` but `ledger_entries: []`.

**Cause:** The `Payment.ledger_entries` relationship is `selectin`-loaded.
After the service inserts a `LedgerEntry` via the repository (FK-only,
not appended to the relationship collection), the loaded relationship
collection is stale.

**Fix:** The route re-reads via `svc.get_payment_detail(payment_id)` after
commit. If you refactor the route to return the service's return value
directly, you will reintroduce this bug.

### 7.6 `Idempotency-Key` header rejected with 422

**Symptom:** `POST /payments` returns 422 even though the header looks
present.

**Cause:** The dependency `get_idempotency_key` requires the header with
alias `Idempotency-Key` (capital I, capital K), `min_length=1`,
`max_length=64`. Empty values, values longer than 64 chars, or the wrong
casing will be rejected.

**Fix:** Send exactly `Idempotency-Key: <value>` with 1-64 characters.

---

## 8. Test inventory

72 tests across 9 files. Run `pytest --collect-only -q` for the full
list. Coverage by layer:

| File | Layer | Count | What it covers |
|---|---|---|---|
| `tests/test_enums.py` | domain | 5 | enum values, str-Enum base, db module exports |
| `tests/test_db_config.py` | config | 6 | DB env/DSN composition, `postgres://` normalization, engine settings |
| `tests/test_alembic.py` | migrations | 4 | Alembic upgrade and key DDL constraints/indexes |
| `tests/test_models.py` | ORM | 14 | tables, columns, constraints, FKs, JSONB, UUID, native enums, defaults |
| `tests/test_schemas.py` | pydantic | 10 | amount validation, required fields, nested serialization |
| `tests/test_repositories.py` | data access | 9 | CRUD, idempotency lookup, versioned update, stale-version guard |
| `tests/test_payment_service.py` | service | 10 | idempotent create, conflict, settle, rollback, state guards, audit payload |
| `tests/test_health.py` | API | 2 | healthz, OpenAPI route surface |
| `tests/test_payments_api.py` | API | 12 | all 4 routes end-to-end, status codes, idempotency, validation |

When adding a feature, add tests at the lowest layer that can verify the
behavior (service for domain rules, API for HTTP contracts). Avoid
duplicating the same assertion across layers.

---

## 9. Commit & branch conventions

This repository is maintained using the superpowers workflow: design spec
-> implementation plan -> TDD per task -> merge. Future work should
follow the same pattern:

1. Brainstorm -> write spec to `docs/superpowers/specs/`.
2. Write plan to `docs/superpowers/plans/`.
3. Branch from `main`, implement task-by-task with tests.
4. Verify full suite passes before merge.
5. `--no-ff` merge into `main` with a descriptive merge commit.

Commit message prefix convention: `feat:`, `fix:`, `docs:`, `chore:`,
`refactor:`, `test:`.
