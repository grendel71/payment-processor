# PostgreSQL Refactor Design

**Date:** 2026-06-19
**Status:** approved
**Supersedes (for the data layer):** §3 of `docs/MAINTENANCE.md`,
§6 of `docs/API_REFERENCE.md`. The payments domain model itself is
unchanged — see `docs/superpowers/specs/2026-05-28-payments-domain-design.md`
for the entity/state-machine design that this refactor preserves.

---

## 1. Overview

Refactor the payments platform's backing store from local SQLite to
PostgreSQL 16, introduce Alembic migrations as the single source of
truth for the database schema, preserve the existing transactional
repository/service patterns, and provide sample containerization for
testing.

### 1.1 Goals

- Replace SQLite with PostgreSQL 16 as the runtime and test backend.
- Alembic migrations own schema creation. `Base.metadata.create_all()`
  is removed from both production startup and test fixtures.
- Preserve the existing repository pattern, service-layer transactional
  scope, and idempotency/optimistic-concurrency guarantees.
- Provide a non-root Dockerfile for the FastAPI app and a
  `docker-compose.yml` bringing up Postgres + the app, suitable for
  local development and CI.
- Switch the test suite to Postgres-only, validating real migrations
  against real Postgres DDL (native enums, JSONB, FK `ondelete` rules).

### 1.2 Non-goals

- **No async refactor.** SQLAlchemy stays sync (`psycopg2`). An
  asyncpg/AsyncSession rewrite is a separate, future effort.
- **No schema changes.** The five existing tables (`payments`,
  `ledger_entries`, `audit_events`, `settlements`,
  `settlement_payments`) keep their columns, constraints, and FKs.
- **No new API routes** and no changes to request/response shapes.
- **No merchant model, no refund flow, no settlement API** — same
  non-goals as the v1 design spec.

---

## 2. Current state (what changes)

| Area | Current | After refactor |
|---|---|---|
| Backing store | SQLite (`paymentprocessor.db`) | PostgreSQL 16 |
| Schema creation | `Base.metadata.create_all()` in `app/main.py:26` lifespan + test fixtures | Alembic migrations only |
| `DATABASE_URL` | Hardcoded `sqlite:///./paymentprocessor.db` in `app/db.py:19` | Env-driven; `DATABASE_URL` overrides, else composed from `POSTGRES_*` vars |
| `connect_args` | `{"check_same_thread": False}` (SQLite-only) | Removed; `pool_pre_ping=True` added |
| Models | Already Postgres-portable (`Uuid`, `JSON`, `Enum` w/ `values_callable`) | Unchanged |
| Repository pattern | `PaymentRepository`, `LedgerEntryRepository`, `AuditEventRepository` | Unchanged |
| Service-layer tx scope | `PaymentService` writes across repos in one session; API commits; `get_db` rolls back on exception | Unchanged |
| Test backend | In-memory SQLite per test via `isolated_db` fixture (`StaticPool`) | Postgres 16 via compose; per-test `TRUNCATE` |
| Test schema setup | `Base.metadata.create_all()` per test (`setup_function`) | `alembic upgrade head` once per session |
| Containerization | None (empty `infra/`, `k8s/`, `charts/` dirs) | Multi-stage Dockerfile (non-root) + `docker-compose.yml` with `db` + `app` |
| Migration tooling | `alembic` listed in `requirements.txt` but not wired up | `alembic.ini` + `migrations/` with `env.py` + initial revision |

---

## 3. Architecture delta

### 3.1 File layout (new + modified)

```
.
├── Dockerfile                       # NEW — multi-stage, non-root
├── docker-compose.yml               # NEW — db (postgres:16-alpine) + app
├── .dockerignore                    # NEW
├── .env.example                     # NEW — env-var template (no secrets)
├── alembic.ini                      # NEW — sqlalchemy.url left empty
├── migrations/                      # NEW
│   ├── env.py                       #    reads DSN via app.db._build_dsn()
│   ├── script.py.mako
│   └── versions/
│       └── 0001_initial_schema.py   #    autogenerate of 5 existing tables
├── requirements.txt                 # mod — pin psycopg2-binary, alembic, add docker-only path
├── app/
│   ├── db.py                        # mod — env-driven DSN, pool_pre_ping, no SQLite connect_args
│   └── main.py                      # mod — remove create_all from lifespan
└── tests/
    └── conftest.py                  # mod — session PG fixture, alembic upgrade, TRUNCATE per test
```

### 3.2 `app/db.py` after refactor

```python
import os
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def _build_dsn() -> str:
    """Construct a Postgres DSN from environment.

    `DATABASE_URL` (if set) wins; otherwise compose from individual
    `POSTGRES_*` vars. Each var has a dev-safe default matching
    `.env.example` so module import never crashes even without env
    configured (production simply overrides). Single source of truth —
    `migrations/env.py` imports this function rather than duplicating.
    """
    if url := os.getenv("DATABASE_URL"):
        return url
    user = os.getenv("POSTGRES_USER", "pp")
    password = os.getenv("POSTGRES_PASSWORD", "pp")
    db = os.getenv("POSTGRES_DB", "paymentprocessor")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db}"


engine = create_engine(_build_dsn(), pool_pre_ping=True, future=True)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    future=True,
    expire_on_commit=False,
)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a session, rolling back on error."""
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
```

**Design notes:**
- `pool_pre_ping=True` guards against stale Postgres connections after
  idle periods or container lifecycle events (important once the app is
  containerized).
- `expire_on_commit=False` is preserved — the existing
  `DetachedInstanceError` troubleshooting note in MAINTENANCE.md still
  applies.
- The `_build_dsn()` helper is importable so `migrations/env.py` reuses
  it without duplicating env-var parsing.
- No backward-compat SQLite path. The platform is Postgres-only.

### 3.3 `app/main.py` after refactor

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Alembic owns schema creation; no create_all here.
    yield
```

The lifespan no longer creates tables. In production, migrations are run
out-of-band (`alembic upgrade head` as a deployment step, not at app
boot). In tests, the session-scoped fixture runs `alembic upgrade head`.

---

## 4. Migration workflow (Alembic)

### 4.1 Layout

```
alembic.ini                # script_location = migrations, sqlalchemy.url = (empty)
migrations/
├── env.py                 # target_metadata = Base.metadata; imports _build_dsn from app.db
├── script.py.mako
└── versions/
    └── 0001_initial_schema.py
```

### 4.2 `alembic.ini` rules

- `script_location = migrations`
- `sqlalchemy.url =` is **left empty**. `env.py` builds the DSN at
  runtime via `app.db._build_dsn()`. No DSN ever appears in the file.
- `prepend_sys_path = .`
- `file_template = %%(year)d%%(month).2d%%(day).2d_%%(rev)s_%%(slug)s`
  for human-readable revision filenames.

### 4.3 `migrations/env.py` contract

- `from app.db import Base, _build_dsn` and set
  `target_metadata = Base.metadata`.
- Import every model module so all tables register on `Base.metadata`
  before autogenerate:
  ```python
  from app.models import audit_event, ledger_entry, payment, settlement  # noqa: F401
  ```
- `run_migrations_offline()` — emits SQL using `context.configure()` +
  `context.run_migrations()`, DSN via `_build_dsn()`.
- `run_migrations_online()` — `create_engine(_build_dsn(), ...)` with
  `connection.run_sync(do_run_migrations)`.
- Same `pool_pre_ping=True` flag used in `app/db.py` for consistency.

### 4.4 Initial migration `0001_initial_schema`

Generated via `alembic revision --autogenerate -m "initial schema"`.
The output is reviewed by hand before commit. It must produce:
- Tables: `payments`, `ledger_entries`, `audit_events`, `settlements`,
  `settlement_payments`.
- Constraints: `uq_merchant_idempotency`
  (`payments(merchant_id, idempotency_key)`),
  `uq_settlement_payment_payment` (`settlement_payments(payment_id)`).
- Foreign keys: `ledger_entries.payment_id → payments.id` and
  `audit_events.payment_id → payments.id` with `ondelete=RESTRICT`.
  `settlement_payments.settlement_id → settlements.id` with
  `ondelete=CASCADE`.
- Indexes: `payments.merchant_id`, `ledger_entries.payment_id`,
  `audit_events.payment_id`.
- Native Postgres enums: `payment_status`, `ledger_entry_type`,
  `audit_event_type`.
- The `payload` column on `audit_events` materializes as `JSONB`
  (SQLAlchemy's `JSON` type maps to `JSONB` on Postgres).

The `downgrade()` must drop all five tables + enum types in
dependency-safe order (`settlement_payments` → `settlements` →
`audit_events` → `ledger_entries` → `payments`), then drop the enum
types.

### 4.5 Developer commands

```bash
# 1. Bring up Postgres
docker compose up -d db

# 2. Apply schema to the dev DB
alembic upgrade head

# 3. After model change: generate a new migration
alembic revision --autogenerate -m "describe change"

# 4. Apply the new migration
alembic upgrade head

# Roll back the latest migration
alembic downgrade -1
```

---

## 5. ACID guarantees and rollback scenarios

The platform already relies on these guarantees; this refactor makes
them real by enforcing them on a database that enforces them.

| Concern | What enforces it | Tests (existing, verifying) |
|---|---|---|
| **Atomicity of payment state-change + ledger + audit** | Single SQLAlchemy `Session` across `PaymentService.create_payment` / `settle_payment`. API route calls `session.commit()` once. Any exception → `get_db`'s `except` branch calls `session.rollback()`. | `test_failed_settlement_writes_failure_audit_and_marks_failed` (force ledger insert to raise → payment stays `pending`, version 1, no audit row) |
| **Consistency of idempotency** | Unique constraint `uq_merchant_idempotency` on `payments(merchant_id, idempotency_key)` is the last line of defense. Postgres strictly enforces this even when SQLite ignores concurrent inserts under `StaticPool`. The service also pre-checks for a friendly error path. | `test_create_duplicate_idempotency_raises`, `test_duplicate_create_different_amount_409` |
| **Isolation of concurrent settlement** | `PaymentRepository.update_with_version` issues `UPDATE ... WHERE id=? AND version=?`. Under Postgres, the row is locked atomically by the UPDATE, so a concurrent writer cannot both see `version=1`. Zero rows matched → `StaleVersionError` → `ConcurrencyError` → 409. | `test_update_with_version_detects_stale_version` |
| **Durability** | Postgres WAL ensures committed transactions survive crashes. SQLite-style "disk write assumed atomic" is no longer in play. | (implicit; integration-tested via the truncate fixture resetting session state) |
| **Ledger immutability** | `LedgerEntry` rows are INSERT-only at the application layer (no UPDATE/DELETE in `LedgerEntryRepository`). Now combined with PG-rigorous FK `ondelete=RESTRICT` (which SQLite silently ignored) preventing deletion of a payment that has ledger entries. A future hardening step (out of scope) will `REVOKE UPDATE, DELETE` at the DB role. | `test_ledger_entry_fk_and_columns` (extended to assert `ondelete=RESTRICT` is emitted) |
| **Migration rollback** | `alembic downgrade -1` reverses the latest revision. Each migration ships with a working `downgrade()`. Migrations that drop data are flagged in their commit message. | `test_alembic_upgrade_head_creates_all_tables` (new) verifies `alembic upgrade head` produces the expected schema; `alembic downgrade -1` returns to empty is exercised during a manual smoke test. |

### Rollback scenarios (per the prompt)

| Scenario | Behavior |
|---|---|
| Failed ledger write during settle | Service raises; `get_db` calls `session.rollback()`. Payment stays `pending` (version 1), no audit for settlement, no ledger row. UI/API client retries. |
| Failed audit write during settle | Same — atomic tx rolls back the status change and the ledger row. |
| Concurrent settle (two workers, same payment) | First writer succeeds (version 1 → 2). Second writer's `update_with_version` matches zero rows (version no longer 1); raises `ConcurrencyError` → 409 → client retries. |
| Migration partially fails mid-deploy | Postgres wraps each `alembic upgrade` step in a DDL transaction; the database is never left half-migrated. |

---

## 6. Test strategy (Postgres-only, replacing SQLite)

### 6.1 Fixture layering (`tests/conftest.py` rewritten)

```
@pytest.fixture(scope="session")
def docker_pg() -> Iterator[Engine]:
    """Spin up Postgres in Docker for the test session if not already up.
    Uses a fixed container name + port; tears down on session exit.
    Yields an engine bound to paymentprocessor_test DB.

    Assumes `docker compose up -d db` has been run (the fixture only
    waits for connectivity and creates the test DB if missing — it does
    not start the container itself, so the same fixture works in CI where
    PG is provisioned out-of-band)."""

@pytest.fixture(scope="session")
def engine(docker_pg) -> Engine:
    """Run `alembic upgrade head` once against the test DB; yield engine."""

@pytest.fixture(autouse=True, scope="function")
def truncate_tables(engine) -> Iterator[None]:
    """TRUNCATE all tables between tests. Autouse; fast on Postgres.
    Uses TRUNCATE ... RESTART IDENTITY CASCADE."""

@pytest.fixture()
def db(engine) -> Iterator[Session]:
    """Yields a Session bound to engine."""

@pytest.fixture()
def client(engine, truncate_tables) -> Iterator[TestClient]:
    """FastAPI TestClient with app bound to the test engine.
    Reseats app.db.engine / SessionLocal (preserving the existing
    lazy-binding pattern in app/api/deps.py)."""
```

**Test database isolation:** the `docker_pg` fixture connects to a
separate `paymentprocessor_test` database (distinct from the dev
`paymentprocessor` database). If that database does not exist, the
fixture creates it by connecting to the always-present `postgres`
maintenance database and issuing `CREATE DATABASE`. This lets the
same `docker compose up -d db` service serve both dev sessions and
test sessions without data collision.

### 6.2 Changes to existing test files

| File | Change |
|---|---|
| `tests/conftest.py` | Rewritten per layering above. Drops `StaticPool` + `isolated_db`; replaces with `docker_pg` + `engine` + `truncate_tables` + `db` + `client`. |
| `tests/test_models.py` | Remove `setup_function` drop/create calls. Use `engine` fixture for schema introspection. Existing assertions stand. |
| `tests/test_repositories.py` | Remove `setup_function`. Use `db` fixture for sessions instead of `SessionLocal()`. |
| `tests/test_payment_service.py` | Remove `setup_function`. Use `db` fixture. |
| `tests/test_payments_api.py` | Unchanged assertions; uses `client` fixture. |
| `tests/test_enums.py`, `tests/test_schemas.py`, `tests/test_health.py` | Unchanged — pure logic, no DB. |

### 6.3 New Postgres-specific tests

| Test | File | What it pins |
|---|---|---|
| `test_payment_status_enum_is_pg_native` | `tests/test_models.py` | `payment_status` enum is a Postgres type (`information_schema.typeregister`), not a CHECK constraint |
| `test_ledger_entry_fk_ondelete_restrict` | `tests/test_models.py` | `ledger_entries.payment_id` FK has `ondelete=RESTRICT` (SQLite silently ignored this) |
| `test_audit_payload_jsonb` | `tests/test_models.py` | `audit_events.payload` column type is `JSONB` (enables GIN indexing later) |
| `test_uuid_column_pg_uuid` | `tests/test_models.py` | `payments.id`, `merchant_id` columns are native PG `UUID`, not `VARCHAR(36)` |
| `test_alembic_upgrade_head_creates_all_tables` | `tests/test_alembic.py` (new file) | `alembic upgrade head` against an empty DB produces all 5 tables with expected constraints |

### 6.4 Test execution

```bash
# From a clean checkout:
docker compose up -d db            # spin up Postgres
.venv/bin/pytest -v                # full suite
.venv/bin/pytest tests/test_alembic.py -v   # migration-specific
```

CI equivalent: set `POSTGRES_*` env vars pointing at the CI's PG
service; `pytest` runs directly against it.

---

## 7. Containerization

### 7.1 `Dockerfile` (multi-stage, non-root)

```dockerfile
# Stage 1: build deps in a throwaway image
FROM python:3.12-slim AS builder
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# Stage 2: runtime — no build tools, non-root user
FROM python:3.12-slim AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app && useradd --system --gid app --home-dir /app app
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY app ./app
COPY alembic.ini ./
COPY migrations ./migrations/
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**Notes:**
- Two stages: builder pulls in `gcc` and `libpq-dev` to compile
  `psycopg2-binary`'s C extension; runtime carries only `libpq5`.
- Non-root: a `system` user/group named `app`. Per AGENTS.md
  Kubernetes standards: "use non-root containers."
- `alembic.ini` + `migrations/` are copied into the image so the same
  image can run `alembic upgrade head` (as an init container or a
  one-shot job) without needing the source tree.
- `requirements.txt` only; `requirements-dev.txt` (pytest, httpx) is
  not installed in the runtime image — tests run on the host or in a
  separate CI image.

### 7.2 `docker-compose.yml`

```yaml
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-pp}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-pp}
      POSTGRES_DB: ${POSTGRES_DB:-paymentprocessor}
    ports:
      - "${POSTGRES_PORT:-5432}:5432"
    volumes:
      - pp_pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-pp} -d ${POSTGRES_DB:-paymentprocessor}"]
      interval: 5s
      timeout: 3s
      retries: 10

  app:
    build: .
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-pp}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-pp}
      POSTGRES_DB: ${POSTGRES_DB:-paymentprocessor}
      POSTGRES_HOST: db
      POSTGRES_PORT: "5432"
    ports:
      - "8000:8000"
    depends_on:
      db:
        condition: service_healthy

volumes:
  pp_pgdata:
```

**Design notes:**
- `db` uses `postgres:16-alpine` (small base, sufficient for dev/test).
- Healthcheck on `db` ensures the `app` service only starts when PG is
  accepting connections.
- The `app` service uses `POSTGRES_HOST: db` so the container-to-
  container DNS works.
- A named volume persists PG data across `docker compose down`.
- No `test` service: tests run on the host (`.venv/bin/pytest`) against
  the `db` service. This matches the project's existing local-dev
  pattern and CI expectations.

### 7.3 `.env.example`

```
POSTGRES_USER=pp
POSTGRES_PASSWORD=pp
POSTGRES_DB=paymentprocessor
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
```

Sample values only, not secrets. Real deployments inject via Secret
manager / Kubernetes Secret.

### 7.4 `.dockerignore`

Excludes `.venv`, `.git`, `__pycache__`, `*.db`, `.pytest_cache`,
`.direnv`, `.worktrees`, `.opencode`, `tests/` (the runtime image does
not ship tests).

---

## 8. Implementation phasing

The implementation plan (separate doc, `docs/superpowers/plans/2026-06-19-postgresql-refactor-implementation.md`)
breaks the work into TDD-driven tasks:

1. **Env-driven `app/db.py`** — `_build_dsn()` from env, `pool_pre_ping`,
   drop SQLite `connect_args`. Update env tests.
2. **Alembic scaffold** — `alembic.ini`, `migrations/env.py`,
   `script.py.mako`. Add a smoke test that `alembic upgrade head`
   creates the expected tables (against the dockerized PG).
3. **Initial migration** — `alembic revision --autogenerate` of the
   existing schema; hand-review and commit.
4. **Remove `create_all` from lifespan** — `app/main.py` no longer
   bootstraps schema; tests/production now depend on Alembic.
5. **Rewrite `tests/conftest.py`** — session-scoped docker-pg fixture,
   alembic-upgrade-once, truncate-per-test, `db` + `client` fixtures.
6. **Migrate existing DB-touching tests** — drop `setup_function`,
   rewire sessions to the `db` fixture.
7. **Add Postgres-specific assertions** — native enum, FK ondelete,
   JSONB, native UUID. Add `tests/test_alembic.py`.
8. **Containerization** — `Dockerfile`, `docker-compose.yml`,
   `.dockerignore`, `.env.example`.
9. **Update docs** — reflect Postgres/Alembic/docker reality in
   `docs/MAINTENANCE.md` and `docs/API_REFERENCE.md`.
10. **End-to-end verification** — full suite green, manual smoke of
    `docker compose up` + `curl /payments`, confirm no hardcoded
    secrets.

---

## 9. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Autogenerate produces a subtly wrong migration (missing index, wrong cascade direction) | Hand-review of `0001_initial_schema.py` before commit. New `tests/test_alembic.py` asserts the expected constraint set. |
| Test suite becomes slower (network round-trips vs in-memory) | TRUNCATE is fast on a local PG container; only one `alembic upgrade` per session, not per test. Tradeoff accepted: correctness > raw speed. Tests require `docker compose up db` (documented in MAINTENANCE.md). |
| Env-var layout differs across dev/CI/prod | `DATABASE_URL` env override lets CI/prod provide a single var; `POSTGRES_*` compose is the dev-friendly path. Documented in `.env.example`. |
| Existing tests rely on SQLite-specific behavior (e.g. `IntegrityError` exact phrasing) | Audit during task 6; if any test asserts SQLite text, rewrite to assert HTTP-level behavior (409) or the Python exception class only. |
| `psycopg2-binary` wheel compatibility on slim image | Builder stage installs `gcc` + `libpq-dev`; runtime carries `libpq5`. Verified during the container-build task. |
| Order-of-imports issue in `migrations/env.py` causes `target_metadata` to be empty during autogenerate | `env.py` explicitly imports all model modules (documented in §4.3). |
| `pool_pre_ping` adds latency to every check-out | The check is one cheap `SELECT 1`; acceptable tradeoff for resilience against stale connections. |
| Hardcoded secrets in compose / Dockerfile | Compose uses env-var substitution (`${POSTGRES_PASSWORD}`); `.env.example` ships only placeholder values. `.gitignore` ensures `.env` is never committed. |
| Existing `MAINTENANCE.md` documents SQLite workflows that will be wrong | Update MAINTENANCE.md and API_REFERENCE.md as task 9 of the plan. |

---

## 10. Out-of-scope future work

Collected here so the design spec records them, but explicitly not in
this refactor:

- Async SQLAlchemy refactor (`asyncpg`, `AsyncSession`, async service
  methods).
- DB-level ledger immutability (`REVOKE UPDATE, DELETE` on
  `ledger_entries` / `audit_events`, or triggers).
- Merchant model + FK on `payments.merchant_id`.
- Currency column.
- Refund flow, settlement API, `SETTLEMENT_FAILED` route.
- Kubernetes Helm chart (directories exist but are empty — a future
  task per `.opencode/instructions/helm.md`).
- Terraform infra (per `.opencode/instructions/terraform.md`).
- Structured logging / observability.
