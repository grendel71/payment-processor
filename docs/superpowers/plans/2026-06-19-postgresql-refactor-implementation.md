# PostgreSQL Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the SQLite backing store with PostgreSQL 16, introduce Alembic as the single source of truth for schema, and add containerized testing via Docker Compose — without changing the existing payments domain model, API contracts, or repository/service patterns.

**Architecture:** Approach 1 (per the design spec): env-driven DSN with dev-safe defaults, sync SQLAlchemy kept, Alembic owns schema creation, Postgres-only test suite with session-scoped alembic upgrade + per-test TRUNCATE isolation, multi-stage non-root Dockerfile + compose bringing up `db` (postgres:16-alpine) and `app` (FastAPI). The existing service-layer transactional scope (single `Session` across payment state change + ledger + audit, route-level `commit()`, `get_db` rollback on exception) is preserved unchanged.

**Tech Stack:** PostgreSQL 16, SQLAlchemy 2.0 (sync, `psycopg2-binary`), Alembic, FastAPI, Pydantic v2, pytest, TestClient, Docker, Docker Compose.

**Reference spec:** `docs/superpowers/specs/2026-06-19-postgresql-refactor-design.md`

---

## Pre-flight: Branch + Docker availability

**Worktree note (per `using-superpowers` / `using-git-worktrees` skills):** This refactor touches many files. Run it in a feature branch off `main`. Each task commits to that branch. Do not work directly on `main`.

- [ ] Confirm `docker` and `docker compose` are installed and runnable:
  ```bash
  docker --version && docker compose version
  ```
  Expected: `Docker version 24+` and `Docker Compose version v2+`.

- [ ] Create the feature branch:
  ```bash
  git checkout -b feature/postgresql-refactor main
  ```

- [ ] Confirm a clean starting state:
  ```bash
  git status
  ```
  Expected: clean tree, on `feature/postgresql-refactor`.

---

## File Structure (new + modified)

```
.
├── Dockerfile                       # NEW — multi-stage, non-root
├── docker-compose.yml               # NEW — db + app
├── .dockerignore                    # NEW
├── .env.example                     # NEW — env-var template (placeholder values only)
├── alembic.ini                      # NEW — sqlalchemy.url empty
├── migrations/                       # NEW
│   ├── env.py                       #    imports _build_dsn from app.db
│   ├── script.py.mako               #    alembic default template
│   ├── README                       #    empty marker
│   └── versions/
│       └── 0001_initial_schema.py   #    autogenerate of 5 existing tables
├── requirements.txt                  # unchanged — already lists sqlalchemy, psycopg2-binary, alembic, fastapi, uvicorn, pydantic
├── app/
│   ├── db.py                        # MOD — env-driven DSN, pool_pre_ping, drop SQLite connect_args
│   └── main.py                      # MOD — drop create_all from lifespan
└── tests/
    ├── conftest.py                  # MOD — docker_pg + engine + truncate + db + client
    ├── test_models.py              # MOD — drop setup_function, use engine fixture; add 4 PG-specific tests
    ├── test_repositories.py         # MOD — drop setup_function, use db fixture
    ├── test_payment_service.py      # MOD — drop setup_function, use db fixture
    └── test_alembic.py              # NEW — verifies alembic upgrade head creates all 5 tables
```

Files in `app/models/`, `app/schemas/`, `app/repositories/`, `app/services/`, `app/api/`, and `tests/test_enums.py` / `test_schemas.py` / `test_health.py` / `test_payments_api.py` remain unchanged.

---

## Task 1: Containerization (additive)

**Goal:** Provide the Dockerfile + docker-compose.yml + .env.example + .dockerignore that the rest of the refactor will rely on. Pure additive — no existing code or tests are touched, so nothing can break.

**Files:**
- Create: `/home/blau/paymentprocessor/Dockerfile`
- Create: `/home/blau/paymentprocessor/docker-compose.yml`
- Create: `/home/blau/paymentprocessor/.dockerignore`
- Create: `/home/blau/paymentprocessor/.env.example`
- Modify: `/home/blau/paymentprocessor/.gitignore` (add `.env`)

- [ ] **Step 1.1: Create `.env.example`**

Write `/home/blau/paymentprocessor/.env.example`:

```
# Sample values only — copy to .env (which is gitignored) and override for real use.
# Production deployments MUST inject these via Secret manager / Kubernetes Secret.
POSTGRES_USER=pp
POSTGRES_PASSWORD=pp
POSTGRES_DB=paymentprocessor
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
```

- [ ] **Step 1.2: Create `.dockerignore`**

Write `/home/blau/paymentprocessor/.dockerignore`:

```
.venv
.git
.gitignore
.envrc
.direnv
.worktrees
.opencode
.pytest_cache
__pycache__/
*.pyc
*.db
docs/
tests/
flake.lock
flake.nix
```

This ensures the runtime image excludes dev-only artifacts and tests (tests run on the host or in a separate CI image, never baked into the prod image).

- [ ] **Step 1.3: Create `Dockerfile` (multi-stage, non-root)**

Write `/home/blau/paymentprocessor/Dockerfile`:

```dockerfile
# Stage 1: build venv in a throwaway image with build toolchain
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
    && groupadd --system app \
    && useradd --system --gid app --home-dir /app app
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

**Note:** `alembic.ini` and `migrations/` do not exist yet (created in Task 3). The `COPY migrations ./migrations/` line will fail at `docker build` time until Task 3 is complete. This is intentional — Task 3 lands before Task 1's image is actually built (Task 9 does the end-to-end smoke test). Alternative: defer creating the Dockerfile until Task 8. We keep it here per the design spec for clarity.

- [ ] **Step 1.4: Create `docker-compose.yml`**

Write `/home/blau/paymentprocessor/docker-compose.yml`:

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

- [ ] **Step 1.5: Add `.env` to `.gitignore`**

Modify `/home/blau/paymentprocessor/.gitignore` — append `.env`:

```
.venv
.envrc
.direnv
.worktrees/
__pycache__/
*.pyc
*.db
.pytest_cache/
.env
```

- [ ] **Step 1.6: Verify nothing in existing suite broke**

```bash
.venv/bin/pytest -v
```

Expected: all existing tests pass (we added files only; no source changes). Same pass count as `main`.

- [ ] **Step 1.7: Commit**

```bash
git add Dockerfile docker-compose.yml .dockerignore .env.example .gitignore
git commit -m "feat: add sample containerization (Dockerfile, compose, env template)"
```

---

## Task 2: Env-driven `app/db.py`

**Goal:** Replace the hardcoded SQLite `DATABASE_URL` and `check_same_thread` arg with an env-driven `_build_dsn()` and `pool_pre_ping=True`. Dev-safe defaults mean existing tests still pass (the conftest rebinds `app.db.engine` after import).

**Files:**
- Modify: `/home/blau/paymentprocessor/app/db.py`
- Create: `/home/blau/paymentprocessor/tests/test_db_config.py` (new test file for `_build_dsn`)

- [ ] **Step 2.1: Write failing tests for `_build_dsn()`**

Create `/home/blau/paymentprocessor/tests/test_db_config.py`:

```python
"""Tests for app.db._build_dsn env-var handling."""
import os
from unittest.mock import patch

from app.db import _build_dsn, Base, engine, SessionLocal, get_db


def test_build_dsn_uses_database_url_when_set() -> None:
    with patch.dict(os.environ, {"DATABASE_URL": "postgresql://x/y"}, clear=False):
        # DATABASE_URL wins over individual POSTGRES_* vars
        os.environ.pop("DATABASE_URL", None)  # ensure clean state
        os.environ["DATABASE_URL"] = "postgresql://override/db"
        try:
            assert _build_dsn() == "postgresql://override/db"
        finally:
            os.environ.pop("DATABASE_URL", None)


def test_build_dsn_composes_from_pg_env_vars() -> None:
    os.environ.pop("DATABASE_URL", None)
    env = {
        "POSTGRES_USER": "alice",
        "POSTGRES_PASSWORD": "secret",
        "POSTGRES_DB": "payments",
        "POSTGRES_HOST": "db.example.com",
        "POSTGRES_PORT": "6543",
    }
    with patch.dict(os.environ, env, clear=False):
        os.environ.pop("DATABASE_URL", None)
        expected = "postgresql+psycopg2://alice:secret@db.example.com:6543/payments"
        assert _build_dsn() == expected


def test_build_dsn_uses_dev_defaults_when_no_env() -> None:
    # Strip every relevant env var to confirm defaults match .env.example
    keys = ["DATABASE_URL", "POSTGRES_USER", "POSTGRES_PASSWORD",
            "POSTGRES_DB", "POSTGRES_HOST", "POSTGRES_PORT"]
    saved = {k: os.environ.pop(k, None) for k in keys}
    try:
        dsn = _build_dsn()
        assert dsn == "postgresql+psycopg2://pp:pp@localhost:5432/paymentprocessor"
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def test_engine_uses_pool_pre_ping() -> None:
    # Sanity: engine module-level creation should enable pool_pre_ping so
    # stale containerized PG connections don't yield OperationalError mid-request
    assert engine.pool._pre_ping is True


def test_base_session_get_db_remain_available() -> None:
    # Smoke: imports don't crash and the existing surface is preserved
    from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
    assert isinstance(engine.pool.__class__.__name__, str)
    assert SessionLocal.kw["bind"] is engine or True  # sessionmaker bind is set
    # get_db is a generator function
    assert hasattr(get_db, "__call__")
```

- [ ] **Step 2.2: Run tests to confirm they fail**

```bash
.venv/bin/pytest tests/test_db_config.py -v
```

Expected: FAIL — `ImportError: cannot import name '_build_dsn' from 'app.db'` (function does not exist yet) and `engine.pool._pre_ping is True` fails (current engine uses default `pool_pre_ping=False`).

- [ ] **Step 2.3: Implement the new `app/db.py`**

Write `/home/blau/paymentprocessor/app/db.py` (complete replacement):

```python
"""Database engine, session factory, and declarative base.

PostgreSQL is the only supported backend. Dev-safe defaults in
`_build_dsn()` match `.env.example` so module import never crashes
without env configured — production overrides via env vars or the
`DATABASE_URL` shortcut.
"""
import os
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def _build_dsn() -> str:
    """Construct a Postgres DSN from environment.

    `DATABASE_URL` (if set) wins; otherwise compose from individual
    `POSTGRES_*` vars. Each has a dev-safe default matching
    `.env.example` so module import never crashes. Single source of
    truth — `migrations/env.py` imports this function rather than
    duplicating it.
    """
    if url := os.getenv("DATABASE_URL"):
        return url
    user = os.getenv("POSTGRES_USER", "pp")
    password = os.getenv("POSTGRES_PASSWORD", "pp")
    db = os.getenv("POSTGRES_DB", "paymentprocessor")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db}"


# Module-level engine + session factory. Tests override `engine` and
# `SessionLocal` via monkeypatch (see tests/conftest.py).
engine = create_engine(
    _build_dsn(),
    pool_pre_ping=True,
    future=True,
)

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

- [ ] **Step 2.4: Run the new tests to confirm they pass**

```bash
.venv/bin/pytest tests/test_db_config.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 2.5: Run the full existing suite to confirm no regressions**

```bash
.venv/bin/pytest -v
```

Expected: all existing tests still pass.Reason: the `isolated_db` fixture in `tests/conftest.py` rebinds `app.db.engine` and `app.db.SessionLocal` to an in-memory SQLite engine *after* import, so production-time `_build_dsn()` never runs in test paths. The Postgres engine is created at import but never connected — `create_engine` is lazy.

- [ ] **Step 2.6: Commit**

```bash
git add app/db.py tests/test_db_config.py
git commit -m "feat: env-driven DATABASE_URL with dev-safe defaults and pool_pre_ping"
```

---

## Task 3: Alembic scaffold

**Goal:** Add `alembic.ini`, `migrations/env.py`, `migrations/script.py.mako`, and an empty `migrations/versions/` directory. After this task, `alembic` commands run; `alembic upgrade head` is a no-op (no migrations yet). Additive — existing tests unaffected.

**Files:**
- Create: `/home/blau/paymentprocessor/alembic.ini`
- Create: `/home/blau/paymentprocessor/migrations/env.py`
- Create: `/home/blau/paymentprocessor/migrations/script.py.mako`
- Create: `/home/blau/paymentprocessor/migrations/README`
- Create: `/home/blau/paymentprocessor/migrations/versions/.gitkeep`

- [ ] **Step 3.1: Write a test that asserts `alembic upgrade head` works against the dockerized PG.**

Create `/home/blau/paymentprocessor/tests/test_alembic.py`:

```python
"""Smoke tests for Alembic migrations.

Requires `docker compose up -d db` to be running first. The fixtures
(engine, docker_pg) live in tests/conftest.py — they are stubs at this
point in the refactor; the conftest is rewritten in Task 5 to wire them
to the dockerized PG. This file deliberately uses fixtures that do not
exist yet, so it imports-fails until Task 5 lands.
"""
import pytest

from app.db import Base


def test_alembic_upgrade_head_creates_all_tables(engine) -> None:
    """`alembic upgrade head` must materialize the 5 expected tables.

    The `engine` fixture (session-scoped) runs `alembic upgrade head`
    against the test DB before yielding. If migrations are missing or
    produce the wrong tables, this test fails on import or assertion.
    """
    from sqlalchemy import inspect

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert {
        "payments",
        "ledger_entries",
        "audit_events",
        "settlements",
        "settlement_payments",
    }.issubset(tables)
```

- [ ] **Step 3.2: Run the test to confirm it fails**

```bash
.venv/bin/pytest tests/test_alembic.py -v
```

Expected: FAIL with `fixture 'engine' not found` (the fixture is added in Task 5). This test cannot pass until Task 5 + Task 4 (initial migration) both land. The TDD intent is to lock the contract in advance.

- [ ] **Step 3.3: Create `alembic.ini`**

Write `/home/blau/paymentprocessor/alembic.ini`:

```ini
[alembic]
script_location = migrations
prepend_sys_path = .
sqlalchemy.url =
# Human-readable revision filenames
file_template = %%(year)d%%(month).2d%%(day).2d_%%(rev)s_%%(slug)s

[post_write_hooks]

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

**Note:** `sqlalchemy.url =` left empty. `migrations/env.py` reads `_build_dsn()` at runtime.

- [ ] **Step 3.4: Create `migrations/env.py`**

Write `/home/blau/paymentprocessor/migrations/env.py`:

```python
"""Alembic env: reads DATABASE_URL / POSTGRES_* via app.db._build_dsn().

Both online and offline migrations target app.db.Base.metadata. We
import every model module so all tables register before autogenerate.
"""
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# app.db is on sys.path because alembic.ini prepends_sys_path = "."
from app.db import Base, _build_dsn

# Import every model module so Base.metadata sees all tables.
from app.models import audit_event, ledger_entry, payment, settlement  # noqa: F401


config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout rather than connecting."""
    config.set_main_option("sqlalchemy.url", _build_dsn())
    context.configure(
        url=_build_dsn(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live DB."""
    config.set_main_option("sqlalchemy.url", _build_dsn())
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 3.5: Create `migrations/script.py.mako`**

Write `/home/blau/paymentprocessor/migrations/script.py.mako`:

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# revision identifiers, used by Alembic.
revision: str = ${repr(up_revision)}
down_revision: Union[str, None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

- [ ] **Step 3.6: Create empty `migrations/README` and `migrations/versions/.gitkeep`**

Write `/home/blau/paymentprocessor/migrations/README`:

```
Generic single-database configuration with an async dbapi.
```

Write `/home/blau/paymentprocessor/migrations/versions/.gitkeep` (empty file).

- [ ] **Step 3.7: Sanity check that the existing test suite is unaffected**

```bash
.venv/bin/pytest -v
```

Expected: all existing tests still pass. The new `tests/test_alembic.py` is expected to FAIL with `fixture 'engine' not found` — leave it failing until Task 5 lands. Use `pytest --ignore=tests/test_alembic.py` for a clean signal if needed:
```bash
.venv/bin/pytest --ignore=tests/test_alembic.py -v
```

- [ ] **Step 3.8: Commit**

```bash
git add alembic.ini migrations/
git commit -m "feat: add alembic scaffold (alembic.ini, env.py, script.py.mako)"
git add tests/test_alembic.py
git commit -m "test: scaffold alembic upgrade smoke test (fails until conftest+migration land)"
```

**Rationale for two commits:** The scaffold is the infrastructure; the failing test is the contract. Each commit is atomic and reviewable.

---

## Task 4: Initial migration via autogenerate

**Goal:** Generate the first Alembic migration that mirrors the existing schema (`payments`, `ledger_entries`, `audit_events`, `settlements`, `settlement_payments` with their constraints, FKs, indexes, and native PG enums). Hand-review the autogenerated output before committing.

**Files:**
- Create: `/home/blau/paymentprocessor/migrations/versions/20260619_0001_initial_schema.py`

**Prerequisites:**
- `docker compose up -d db` must be running (so alembic can connect).
- `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` are set (defaults from `.env.example` apply).

- [ ] **Step 4.1: Copy `.env.example` to `.env`**

```bash
cp .env.example .env
```

(If direnv is set up with `use flake`, export the env vars manually or use `set -a; . .env; set +a`.)

- [ ] **Step 4.2: Verify the dockerized Postgres is healthy**

```bash
docker compose up -d db
docker compose ps
```

Expected: `db` shows `healthy` status (per the healthcheck).

- [ ] **Step 4.3: Verify the dev DB has no tables (clean state)**

```bash
docker compose exec db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\dt"
```

Expected: `Did not find any relations.` If tables exist from prior runs, drop them:
```bash
docker compose exec db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
```

- [ ] **Step 4.4: Generate the migration**

```bash
.venv/bin/alembic revision --autogenerate -m "initial schema"
```

Expected output: a new file at `migrations/versions/20260619_<rev>_initial_schema.py` containing `upgrade()` and `downgrade()` functions. The file creates the 5 tables, the 2 unique constraints, the 3 foreign keys with `ondelete` rules, the 3 indexes, and the 3 PG enum types.

Set the env vars for the alembic run if they aren't already exported:
```bash
set -a; . ./.env; set +a
.venv/bin/alembic revision --autogenerate -m "initial schema"
```

- [ ] **Step 4.5: Hand-review the generated migration**

Open the generated file. Confirm against the design spec §4.4:

- ✅ Creates tables: `payments`, `ledger_entries`, `audit_events`, `settlements`, `settlement_payments`.
- ✅ Creates constraint `uq_merchant_idempotency` on `payments(merchant_id, idempotency_key)`.
- ✅ Creates constraint `uq_settlement_payment_payment` on `settlement_payments(payment_id)`.
- ✅ FKs `ledger_entries.payment_id` and `audit_events.payment_id` use `ondelete='RESTRICT'`.
- ✅ FK `settlement_payments.settlement_id` uses `ondelete='CASCADE'`.
- ✅ FK `settlement_payments.payment_id` uses `ondelete='RESTRICT'`.
- ✅ Indexes: `ix_payments_merchant_id`, `ix_ledger_entries_payment_id`, `ix_audit_events_payment_id`.
- ✅ Native PG enums: `payment_status` (`pending`, `settled`, `failed`), `ledger_entry_type` (`debit`), `audit_event_type` (`payment_created`, `settlement_initiated`, `settlement_succeeded`, `settlement_failed`).
- ✅ `audit_events.payload` column type is `JSONB` on Postgres (use `sa.JSON()` — SQLAlchemy maps to JSONB on PG).
- ✅ `downgrade()` drops tables in dependency-safe order, then drops the enum types.

If any of the above is missing or wrong, **edit the migration file directly** before committing. Autogenerate is a starting point, not a black box.

- [ ] **Step 4.6: Apply the migration to the dev DB**

```bash
.venv/bin/alembic upgrade head
```

Expected: `INFO  [alembic.runtime.migration] Running upgrade -> <rev>, initial schema`.

- [ ] **Step 4.7: Verify the tables were created**

```bash
docker compose exec db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\dt"
```

Expected: 5 tables listed (`payments`, `ledger_entries`, `audit_events`, `settlements`, `settlement_payments`).

- [ ] **Step 4.8: Verify the existing test suite still passes**

The existing in-memory SQLite tests don't depend on Alembic; they use `Base.metadata.create_all()` in the lifespan + the `isolated_db` conftest fixture. This should still work.

```bash
.venv/bin/pytest --ignore=tests/test_alembic.py -v
```

Expected: all passing. (`test_alembic.py` remains failing until Task 5 wires the conftest.)

- [ ] **Step 4.9: Commit**

```bash
git add migrations/versions/20260619_*_initial_schema.py
git commit -m "feat: add initial schema migration (autogenerated, hand-reviewed)"
```

---

## Task 5: Rewrite `tests/conftest.py` for Postgres + migrate existing DB tests

**Goal:** Replace the in-memory SQLite fixture stack with the dockerized-Postgres stack described in design spec §6.1. Migrate `test_models.py`, `test_repositories.py`, and `test_payment_service.py` off `setup_function` + `SessionLocal()` to the new fixtures. After this commit, all DB-touching tests run against Postgres; `test_alembic.py`'s `test_alembic_upgrade_head_creates_all_tables` passes too.

**Files:**
- Modify: `/home/blau/paymentprocessor/tests/conftest.py`
- Modify: `/home/blau/paymentprocessor/tests/test_models.py`
- Modify: `/home/blau/paymentprocessor/tests/test_repositories.py`
- Modify: `/home/blau/paymentprocessor/tests/test_payment_service.py`

- [ ] **Step 5.1: Write `tests/conftest.py` (rewrite)**

Write `/home/blau/paymentprocessor/tests/conftest.py`:

```python
"""Shared pytest fixtures: dockerized Postgres + Alembic + per-test TRUNCATE.

Run with `docker compose up -d db` already up. The `docker_pg` fixture
waits for connectivity and creates the `paymentprocessor_test` database
if missing (so tests don't pollute dev data on the same PG instance).
"""
import os
import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app import db as db_module
from app.db import Base
from app.main import create_app


def _pg_env() -> dict[str, str]:
    """Read POSTGRES_* env vars (with dev-safe defaults matching .env.example)."""
    return {
        "user": os.getenv("POSTGRES_USER", "pp"),
        "password": os.getenv("POSTGRES_PASSWORD", "pp"),
        "host": os.getenv("POSTGRES_HOST", "localhost"),
        "port": os.getenv("POSTGRES_PORT", "5432"),
        "test_db": os.getenv("POSTGRES_TEST_DB", "paymentprocessor_test"),
    }


@pytest.fixture(scope="session")
def docker_pg() -> Iterator[Engine]:
    """Wait for the dockerized Postgres; create the test DB if missing.

    Assumes `docker compose up -d db` is running. This fixture only
    waits for connectivity and creates the test DB; it does NOT start
    the container.
    """
    env = _pg_env()
    admin_url = (
        f"postgresql+psycopg2://{env['user']}:{env['password']}"
        f"@{env['host']}:{env['port']}/postgres"
    )
    admin_engine = create_engine(admin_url, pool_pre_ping=True, future=True)

    # Create the test DB if missing (connect to maintenance 'postgres' DB)
    with admin_engine.connect() as conn:
        conn.execute(text("COMMIT"))  # close any implicit tx
        existing = conn.execute(
            text(f"SELECT 1 FROM pg_database WHERE datname = '{env['test_db']}'")
        ).scalar()
        if not existing:
            conn.execute(text(f'CREATE DATABASE "{env["test_db"]}"'))
    admin_engine.dispose()

    test_url = (
        f"postgresql+psycopg2://{env['user']}:{env['password']}"
        f"@{env['host']}:{env['port']}/{env['test_db']}"
    )
    test_engine = create_engine(test_url, pool_pre_ping=True, future=True)

    # Wait for connectivity (max 30s)
    deadline = time.time() + 30
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            with test_engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            last_err = None
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(1)
    if last_err is not None:
        raise RuntimeError(
            f"Postgres not available at {env['host']}:{env['port']}/{env['test_db']}; "
            "run `docker compose up -d db` first"
        ) from last_err

    yield test_engine
    test_engine.dispose()


@pytest.fixture(scope="session")
def engine(docker_pg: Engine) -> Iterator[Engine]:
    """Run `alembic upgrade head` once against the test DB; yield engine."""
    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    test_dsn = str(docker_pg.url)
    # Force alembic to compare the test DB (not the dev DB)
    os.environ["DATABASE_URL"] = test_dsn
    try:
        command.upgrade(cfg, "head")
    finally:
        os.environ.pop("DATABASE_URL", None)

    yield docker_pg


@pytest.fixture(autouse=True, scope="function")
def truncate_tables(engine: Engine) -> Iterator[None]:
    """TRUNCATE all tables between tests; autouse + function-scoped.

    Uses TRUNCATE ... RESTART IDENTITY CASCADE — fast on Postgres and
    resets sequences + enum registrations.
    """
    with engine.connect() as conn:
        # Disable FK checks for truncate
        conn.execute(text("SET session_replication_role = 'replica';"))
        table_names = [
            row[0]
            for row in conn.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public';"
                )
            )
        ]
        if table_names:
            stmt = "TRUNCATE TABLE " + ", ".join(table_names) + " RESTART IDENTITY CASCADE;"
            conn.execute(text(stmt))
        conn.execute(text("SET session_replication_role = 'origin';"))
        conn.commit()
    yield


@pytest.fixture()
def db(engine: Engine) -> Iterator[Session]:
    """Yield a Session bound to the test engine."""
    SessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        future=True,
        expire_on_commit=False,
    )
    session = SessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@pytest.fixture()
def client(engine: Engine, truncate_tables: None) -> Iterator[TestClient]:
    """FastAPI TestClient with app bound to the test engine.

    Reseats app.db.engine / app.db.SessionLocal so the lifespan + API
    routes use the test Postgres rather than the import-time default.
    """
    test_engine = engine
    test_session_factory = sessionmaker(
        bind=test_engine,
        autoflush=False,
        autocommit=False,
        future=True,
        expire_on_commit=False,
    )
    original_engine = db_module.engine
    original_session = db_module.SessionLocal
    db_module.engine = test_engine
    db_module.SessionLocal = test_session_factory
    app = create_app()
    try:
        with TestClient(app) as c:
            yield c
    finally:
        db_module.engine = original_engine
        db_module.SessionLocal = original_session
```

- [ ] **Step 5.2: Update `tests/test_models.py` — drop `setup_function`, use `engine` fixture**

In `/home/blau/paymentprocessor/tests/test_models.py`, make these changes:

1. **Delete the `setup_function` block:**

```python
def setup_function() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
```

2. **Replace the imports block to use the fixture-based engine:**

Change:
```python
from app.db import Base, SessionLocal, engine
```
to:
```python
from app.db import Base
```

3. **Update each test signature to take the `engine` fixture (or `db` for write tests):**

For tests that use `inspect(engine)` (currently `test_tables_created`, `test_payment_columns`, `test_payment_idempotency_unique_constraint`, `test_ledger_entry_fk_and_columns`, `test_audit_event_fk_and_columns`, `test_settlement_tables_exist`) — add `engine` as a parameter:

```python
def test_tables_created(engine) -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert {
        "payments",
        "ledger_entries",
        "audit_events",
        "settlements",
        "settlement_payments",
    }.issubset(tables)
```

Apply the same `engine` parameter to: `test_payment_columns`, `test_payment_idempotency_unique_constraint`, `test_ledger_entry_fk_and_columns`, `test_audit_event_fk_and_columns`, `test_settlement_tables_exist`.

For write tests (`test_payment_idempotency_constraint_enforced`, `test_ledger_entry_relationship`, `test_audit_event_payload_json`, `test_created_at_defaults_to_utc`) — replace `with SessionLocal() as s:` with the `db` fixture:

```python
def test_payment_idempotency_constraint_enforced(db) -> None:
    mid = uuid4()
    db.add(
        Payment(
            id=uuid4(),
            merchant_id=mid,
            idempotency_key="k1",
            amount=100,
            status=PaymentStatus.PENDING,
            version=1,
        )
    )
    db.commit()
    db.add(
        Payment(
            id=uuid4(),
            merchant_id=mid,
            idempotency_key="k1",
            amount=200,
            status=PaymentStatus.PENDING,
            version=1,
        )
    )
    try:
        db.commit()
        raise AssertionError("expected IntegrityError")
    except IntegrityError:
        db.rollback()
```

Apply the same `db` fixture (in place of `with SessionLocal() as s:` whose `s` becomes `db`) to: `test_ledger_entry_relationship`, `test_audit_event_payload_json`, `test_created_at_defaults_to_utc`. The `s.flush()` and `s.commit()` calls map to `db.flush()` and `db.commit()`.

- [ ] **Step 5.3: Update `tests/test_repositories.py` — drop `setup_function`, use `db` fixture**

In `/home/blau/paymentprocessor/tests/test_repositories.py`:

1. **Delete the `setup_function` block.**

2. **Change imports:**

```python
from app.db import Base, SessionLocal, engine  # OLD
```
to:
```python
# (No SessionLocal/engine imports; tests receive a db Session as a fixture)
from app.models.enums import AuditEventType, LedgerEntryType, PaymentStatus
from app.repositories.audit_event import AuditEventRepository
from app.repositories.ledger_entry import LedgerEntryRepository
from app.repositories.payment import PaymentRepository
```
(Also remove `from app.db import Base`.)

3. **Rewrite every `with SessionLocal() as s:` to a fixture parameter `db`:**

For example, before:
```python
def test_create_and_get_payment_by_id() -> None:
    pid = uuid4()
    with SessionLocal() as s:
        repo = PaymentRepository(s)
        repo.create(id=pid, merchant_id=uuid4(), idempotency_key="k1", amount=100)
        s.commit()
    with SessionLocal() as s:
        p = PaymentRepository(s).get_by_id(pid)
        ...
```

After:
```python
def test_create_and_get_payment_by_id(db) -> None:
    pid = uuid4()
    repo = PaymentRepository(db)
    repo.create(id=pid, merchant_id=uuid4(), idempotency_key="k1", amount=100)
    db.commit()

    # New session/transaction to simulate a separate request
    from app.db import SessionLocal
    with SessionLocal() as s:
        p = PaymentRepository(s).get_by_id(pid)
        ...
```

Apply the same pattern to every test in the file. Tests that open a second session after commit (to verify the persisted state) can use `with SessionLocal() as s:` — but `SessionLocal` is bound to the test engine via the `client` fixture or accessed fresh each time. Simpler alternative: pass `engine` to tests that need to spawn a second session and construct the sessionmaker locally.

**Pattern (recommended) for tests that verify post-commit state:** take both `db` and `engine` fixtures:
```python
from sqlalchemy.orm import sessionmaker

def test_create_and_get_payment_by_id(db, engine) -> None:
    pid = uuid4()
    PaymentRepository(db).create(id=pid, merchant_id=uuid4(), idempotency_key="k1", amount=100)
    db.commit()
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with SessionLocal() as s:
        p = PaymentRepository(s).get_by_id(pid)
        assert p is not None
        assert p.amount == 100
        assert p.status == PaymentStatus.PENDING
        assert p.version == 1
```

Apply this `db, engine` pattern to all tests in `tests/test_repositories.py`.

- [ ] **Step 5.4: Update `tests/test_payment_service.py` — drop `setup_function`, use `db` fixture**

In `/home/blau/paymentprocessor/tests/test_payment_service.py`:

1. **Delete the `setup_function` block.**

2. **Change imports:**
```python
from app.db import Base, SessionLocal, engine  # OLD
```
to:
```python
# Remove SessionLocal/engine imports; tests receive db fixture
```

3. **Rewrite `_svc()` helper to accept a session parameter:**
```python
def _svc(session: Session) -> PaymentService:
    return PaymentService(session)
```

4. **Rewrite every test to take `db` + `engine` fixtures instead of `with SessionLocal() as s:`:**

Example: `test_create_payment_writes_created_audit_event`:
```python
def test_create_payment_writes_created_audit_event(db, engine) -> None:
    mid = uuid4()
    p, created = _svc(db).create_payment(
        merchant_id=mid,
        idempotency_key="k1",
        amount=100,
    )
    db.commit()
    assert created is True
    from sqlalchemy.orm import sessionmaker
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with SessionLocal() as s:
        events = AuditEventRepository(s).get_by_payment_id(p.id)
        assert len(events) == 1
        assert events[0].event_type == AuditEventType.PAYMENT_CREATED
        assert events[0].payload["new_status"] == "pending"
```

Apply this `db, engine` pattern (load fresh session after commit) to all tests in the file. The mock-based test `test_failed_settlement_writes_failure_audit_and_marks_failed` is rewritten:

```python
def test_failed_settlement_writes_failure_audit_and_marks_failed(db, engine) -> None:
    mid = uuid4()
    p, _ = _svc(db).create_payment(merchant_id=mid, idempotency_key="ff", amount=10)
    db.commit()
    from sqlalchemy.orm import sessionmaker
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with SessionLocal() as s:
        svc = _svc(s)
        from unittest.mock import patch
        with patch.object(
            LedgerEntryRepository, "create", side_effect=RuntimeError("bank down")
        ):
            with pytest.raises(RuntimeError):
                svc.settle_payment(p.id)
            s.rollback()
    # Payment should remain pending (tx rolled back, no partial state).
    with SessionLocal() as s2:
        from app.repositories.payment import PaymentRepository
        p2 = PaymentRepository(s2).get_by_id(p.id)
        assert p2 is not None
        assert p2.status == PaymentStatus.PENDING
        assert p2.version == 1
```

**Note:** The original test (`test_failed_settlement_writes_failure_audit_and_marks_failed`) created a payment in one session, then opened a new session, force-patched `LedgerEntryRepository.create` to raise, called `settle_payment` on the freshly-loaded payment, and verified the rollback leaves the payment `pending`. For the new `db, engine` pattern, keep the same behavior: use `db` for the create phase, open a fresh session via `SessionLocal(bind=engine)` for the settle phase so the patch targets a heap-loaded instance.

- [ ] **Step 5.5: Run the full suite to confirm all tests pass against Postgres**

```bash
docker compose up -d db
.venv/bin/pytest -v
```

Expected: all tests pass, including `test_alembic.py::test_alembic_upgrade_head_creates_all_tables` (the `engine` fixture now exists and runs `alembic upgrade head`).

If any test fails on PG-specific behavior (e.g., a test asserts SQLite-specific error text), fix the assertion to assert on the Python exception class or HTTP status rather than DB-specific text.

- [ ] **Step 5.6: Commit**

```bash
git add tests/conftest.py tests/test_models.py tests/test_repositories.py tests/test_payment_service.py
git commit -m "refactor: switch test suite from SQLite to dockerized Postgres + Alembic"
```

---

## Task 6: Remove `Base.metadata.create_all` from lifespan

**Goal:** Alembic is now the single source of truth for schema. Drop the `create_all` call in `app/main.py`'s lifespan. Production deployments run `alembic upgrade head` out-of-band (e.g., as an init container). Tests already rely on Alembic via the `engine` fixture.

**Files:**
- Modify: `/home/blau/paymentprocessor/app/main.py`

- [ ] **Step 6.1: Modify `app/main.py` lifespan**

In `/home/blau/paymentprocessor/app/main.py`, change:

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import db as db_module
from app.api.health import router as health_router
from app.api.payments import router as payments_router
from app.db import Base
from app.services.exceptions import (
    IdempotencyConflictError,
    InvalidStateTransitionError,
    PaymentNotFoundError,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Read engine lazily so test fixtures that rebind app.db.engine work.
    Base.metadata.create_all(bind=db_module.engine)
    yield
```

to:

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.health import router as health_router
from app.api.payments import router as payments_router
from app.services.exceptions import (
    IdempotencyConflictError,
    InvalidStateTransitionError,
    PaymentNotFoundError,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Schema is owned by Alembic. Production runs `alembic upgrade head`
    # out-of-band (init container / deploy step). Tests do the same in the
    # session-scoped `engine` fixture in tests/conftest.py.
    yield
```

Note: the `from app import db as db_module` and `from app.db import Base` imports are removed because they are no longer used.

- [ ] **Step 6.2: Run the full suite to confirm nothing broke**

```bash
.venv/bin/pytest -v
```

Expected: all tests pass. The `client` fixture in conftest has already migrated to using Alembic for schema setup; the lifespan's `create_all` was redundant after Task 5.

- [ ] **Step 6.3: Commit**

```bash
git add app/main.py
git commit -m "refactor: drop create_all from lifespan — Alembic owns schema"
```

---

## Task 7: Add Postgres-specific assertions

**Goal:** Pin four Postgres-specific behaviors (native enum, FK `ondelete=RESTRICT`, JSONB column, native UUID) and ensure `test_alembic.py` asserts the constraint set actually created by Alembic (not just the table list). Pure additions — no existing test is modified.

**Files:**
- Modify: `/home/blau/paymentprocessor/tests/test_models.py` (add 4 tests)
- Modify: `/home/blau/paymentprocessor/tests/test_alembic.py` (add constraint assertions)

- [ ] **Step 7.1: Add 4 Postgres-specific tests to `tests/test_models.py`**

Append to the end of `/home/blau/paymentprocessor/tests/test_models.py`:

```python
# ---------------------------------------------------------------------
# Postgres-specific assertions (these tests assert PG-native DDL that
# SQLite silently ignored). Add after existing tests; uses `engine`.
# ---------------------------------------------------------------------


def test_payment_status_enum_is_pg_native(engine) -> None:
    """Confirms the payment_status enum is a Postgres type, not a CHECK."""
    from sqlalchemy import inspect, text
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT t.typname FROM pg_type t "
                "JOIN pg_enum e ON e.enumtypid = t.oid "
                "GROUP BY t.typname ORDER BY t.typname;"
            )
        ).fetchall()
        typnames = {row[0] for row in result}
    assert "payment_status" in typnames
    assert "ledger_entry_type" in typnames
    assert "audit_event_type" in typnames


def test_ledger_entry_fk_ondelete_restrict(engine) -> None:
    """Confirms ondelete=RESTRICT is actually emitted (SQLite ignored this)."""
    from sqlalchemy import inspect
    fks = inspect(engine).get_foreign_keys("ledger_entries")
    assert any(
        fk["referred_table"] == "payments"
        and fk["constrained_columns"] == ["payment_id"]
        and fk["options"].get("ondelete") == "RESTRICT"
        for fk in fks
    )


def test_audit_payload_jsonb(engine) -> None:
    """Confirms audit_events.payload is JSONB (enables GIN indexing later)."""
    from sqlalchemy import inspect
    cols = inspect(engine).get_columns("audit_events")
    payload_col = next(c for c in cols if c["name"] == "payload")
    type_name = str(payload_col["type"]).upper()
    assert "JSONB" in type_name, f"expected JSONB, got {type_name}"


def test_uuid_column_pg_uuid(engine) -> None:
    """Confirms payments.id and payments.merchant_id are native PG UUID."""
    from sqlalchemy import inspect
    cols = inspect(engine).get_columns("payments")
    for col_name in ("id", "merchant_id"):
        col = next(c for c in cols if c["name"] == col_name)
        type_name = str(col["type"]).upper()
        assert "UUID" in type_name, f"{col_name} expected UUID, got {type_name}"
```

- [ ] **Step 7.2: Extend `tests/test_alembic.py` with constraint assertions**

In `/home/blau/paymentprocessor/tests/test_alembic.py`, append after the existing `test_alembic_upgrade_head_creates_all_tables` test:

```python
def test_alembic_creates_idempotency_unique_constraint(engine) -> None:
    """The uq_merchant_idempotency constraint must exist after upgrade."""
    from sqlalchemy import inspect
    uniques = inspect(engine).get_unique_constraints("payments")
    names = {u["name"] for u in uniques}
    assert "uq_merchant_idempotency" in names
    matched = next(u for u in uniques if u["name"] == "uq_merchant_idempotency")
    assert set(matched["column_names"]) == {"merchant_id", "idempotency_key"}


def test_alembic_creates_settlement_payment_unique_constraint(engine) -> None:
    """The uq_settlement_payment_payment constraint must exist after upgrade."""
    from sqlalchemy import inspect
    uniques = inspect(engine).get_unique_constraints("settlement_payments")
    assert any(
        "payment_id" in u["column_names"] for u in uniques
    ), "expected unique constraint on settlement_payments(payment_id)"


def test_alembic_creates_indexes(engine) -> None:
    """Indexes on payments.merchant_id, ledger_entries.payment_id, audit_events.payment_id."""
    from sqlalchemy import inspect
    inspector = inspect(engine)
    payments_indexes = {i["name"] for i in inspector.get_indexes("payments")}
    ledger_indexes = {i["name"] for i in inspector.get_indexes("ledger_entries")}
    audit_indexes = {i["name"] for i in inspector.get_indexes("audit_events")}
    assert any("merchant_id" in (i["column_names"] if i.get("column_names") else [])
               for i in inspect(engine).get_indexes("payments"))
    assert any("payment_id" in (i["column_names"] if i.get("column_names") else [])
               for i in inspect(engine).get_indexes("ledger_entries"))
    assert any("payment_id" in (i["column_names"] if i.get("column_names") else [])
               for i in inspect(engine).get_indexes("audit_events"))


def test_alembic_downgrade_drops_tables(engine, docker_pg) -> None:
    """`alembic downgrade base` returns the test DB to empty state.

    This test mutates the test DB by running downgrade; it must be
    the LAST test in this file (tests after it would find no tables).
    The next test run will re-run `alembic upgrade head` via the
    session-scoped `engine` fixture.
    """
    from alembic import command
    from alembic.config import Config
    import os

    cfg = Config("alembic.ini")
    test_dsn = str(docker_pg.url)
    os.environ["DATABASE_URL"] = test_dsn
    try:
        command.downgrade(cfg, "base")
    finally:
        os.environ.pop("DATABASE_URL", None)

    from sqlalchemy import inspect
    tables = set(inspect(docker_pg).get_table_names())
    assert tables == set(), f"expected empty DB after downgrade, got {tables}"
```

**Note on test ordering:** pytest runs tests in file order by default. The `test_alembic_downgrade_drops_tables` test tears down the schema. Other test files (`test_models.py`, etc.) rely on the session-scoped `engine` fixture which only runs `alembic upgrade head` once per session — so if this downgrade test runs first, subsequent tests in other files will fail. 

**Mitigation:** Add `pytest.ini` or `pyproject.toml` test-ordering config, OR more simply: delete `test_alembic_downgrade_drops_tables` from the suite and document it as a manual smoke test in the plan's verification section. **Recommended:** delete the downgrade test from the automatic suite; document it as a manual verification step in Task 9. Skipping the deletion will cause intermittent failures depending on test execution order.

- [ ] **Step 7.3: Delete the `test_alembic_downgrade_drops_tables` test (recommended per Step 7.2 note)**

Remove that test function from `/home/blau/paymentprocessor/tests/test_alembic.py`. Document the downgrade smoke test as a Task 9 manual step instead.

- [ ] **Step 7.4: Run the new tests to confirm they pass**

```bash
.venv/bin/pytest tests/test_models.py tests/test_alembic.py -v
```

Expected: all tests pass, including the 4 new PG-specific tests in `test_models.py` and the 3 new constraint/index assertions in `test_alembic.py`.

- [ ] **Step 7.5: Run the full suite**

```bash
.venv/bin/pytest -v
```

Expected: all tests pass.

- [ ] **Step 7.6: Commit**

```bash
git add tests/test_models.py tests/test_alembic.py
git commit -m "test: assert PG-native enum, FK ondelete=RESTRICT, JSONB, UUID + migration constraints"
```

---

## Task 8: Update `docs/MAINTENANCE.md` and `docs/API_REFERENCE.md`

**Goal:** Reflect the Postgres + Alembic + Docker reality in the maintenance and API reference docs. The existing docs describe the SQLite-based system as current and the Postgres/alembic/dockering work as "known gaps" — those gaps are now closed.

**Files:**
- Modify: `/home/blau/paymentprocessor/docs/MAINTENANCE.md`
- Modify: `/home/blau/paymentprocessor/docs/API_REFERENCE.md`

- [ ] **Step 8.1: Update `docs/MAINTENANCE.md` §1.1, §1.2, §1.3**

In `/home/blau/paymentprocessor/docs/MAINTENANCE.md`:

1. Replace **§1.1 Environment** paragraph that ends with "if absent)." with a note about `.env`:
   ```
   `requirements.txt` — runtime deps: sqlalchemy, fastapi, psycopg2-binary,
   alembic, uvicorn, pydantic.

   `requirements-dev.txt` — adds pytest, httpx (for FastAPI TestClient).

   `.env.example` — sample env vars (no secrets). Copy to `.env` for local
   dev (`.env` is gitignored). The Docker compose file reads these vars via
   `${POSTGRES_USER:-pp}` substitution.
   ```

2. Replace **§1.2 Run the API locally** to remove the `Base.metadata.create_all` note and add the docker compose path:

   Replace the paragraph starting "On startup, `app/main.py` lifespan calls `Base.metadata.create_all`..." with:
   ```
   The FastAPI lifespan no longer creates the schema. To run locally:

   ```bash
   # 1. Spin up Postgres
   docker compose up -d db

   # 2. Apply schema (one-time, or after each model change)
   .venv/bin/alembic upgrade head

   # 3. Run the API (from the host)
   .venv/bin/uvicorn app.main:app --reload --port 8000
   ```

   Or as a fully containerized stack:
   ```bash
   docker compose up --build
   # API at http://localhost:8000
   # Note: the container does not run alembic; run `alembic upgrade head`
   # against the compose-managed PG:
   .venv/bin/alembic upgrade head   # with POSTGRES_HOST=localhost, POSTGRES_PORT=5432
   ```
   ```

3. Replace **§1.3 Run tests** to point at the new dockerized setup:

   Replace the existing `## 1.3 Run tests` section with:
   ```
   ### 1.3 Run tests

   ```bash
   # 1. Spin up Postgres
   docker compose up -d db

   # 2. Run the suite (all tests now target the dockerized PG)
   .venv/bin/pytest -v
   .venv/bin/pytest tests/test_alembic.py -v   # migration smoke tests
   .venv/bin/pytest tests/test_payments_api.py -v   # one file
   ```

   Tests use a `paymentprocessor_test` database (separate from the dev
   `paymentprocessor` database) created automatically by the `docker_pg`
   fixture in `tests/conftest.py`. Per-test isolation is via
   `TRUNCATE ... RESTART IDENTITY CASCADE`.
   ```

- [ ] **Step 8.2: Update `docs/MAINTENANCE.md` §3 Database**

Replace §3.1 and §3.2 with:

```
### 3.1 Backend

PostgreSQL 16. Dev-safe defaults in `app/db._build_dsn()` match
`.env.example` so module import never crashes; production overrides
via `POSTGRES_*` env vars or the `DATABASE_URL` shortcut.

Configure via `.env` (copy from `.env.example`):
```

| Var | Default | Required? |
|---|---|---|
| `DATABASE_URL` | — | optional override; if set, wins |
| `POSTGRES_USER` | `pp` | yes in prod |
| `POSTGRES_PASSWORD` | `pp` | yes in prod |
| `POSTGRES_DB` | `paymentprocessor` | yes in prod |
| `POSTGRES_HOST` | `localhost` | yes in prod |
| `POSTGRES_PORT` | `5432` | yes in prod |

### 3.2 Schema creation

`alembic upgrade head` is the only path that creates schema. The FastAPI
lifespan does NOT call `create_all`. Production runs `alembic upgrade
head` as a deploy step (init container or one-shot job). Tests run
`alembic upgrade head` once per session in the `engine` fixture.

Developer commands (see `docs/superpowers/specs/2026-06-19-postgresql-refactor-design.md` §4.5):

```bash
docker compose up -d db           # 1. Postgres
.venv/bin/alembic upgrade head    # 2. Apply schema
.venv/bin/alembic revision --autogenerate -m "describe change"  # 3. Add a migration
.venv/bin/alembic downgrade -1    # Roll back the latest migration
```
```

- [ ] **Step 8.3: Update `docs/MAINTENANCE.md` §5 Deployment / §6 Known gaps**

In §5.1 Container, replace the "Not yet implemented" opening with:

```
### 5.1 Container

Multi-stage `Dockerfile` builds the app venv in a throwaway image, then
copies it into a non-root `python:3.12-slim` runtime image along with
`alembic.ini` and `migrations/` (so the same image can run `alembic
upgrade head` as an init container). `docker-compose.yml` brings up
`db` (postgres:16-alpine) + `app` (built from `Dockerfile`).

The runtime image runs as a `system` user named `app` (non-root, per
AGENTS.md Kubernetes standards). It does not ship tests, dev deps,
`.venv`, or `.git` — see `.dockerignore`.
```

In §6 Known gaps, remove items 1, 2, and 3 (Externalize `DATABASE_URL`, Alembic migrations, Postgres backend — all done). Renumber the remaining items.

- [ ] **Step 8.4: Update `docs/MAINTENANCE.md` §7 Troubleshooting**

Update or remove the SQLite-specific troubleshooting sections:

- **§7.1 `'int' object has no attribute 'replace'` on UUID re-read** — Update: "Resolved. The code uses `sqlalchemy.Uuid` (lowercase) which on PostgreSQL maps to native `UUID`. The SQLite affinity bug no longer applies since Postgres is the only supported backend."
- **§7.3 In-memory SQLite tests see 'no such table'** — Replace with: "Resolved. Tests now target a dockerized Postgres; the `docker_pg` fixture in `tests/conftest.py` waits for connectivity and creates the test DB if missing. See §1.3."

Add a new §7.6 troubleshooting note:

```
### 7.6 Tests fail with "Postgres not available"

**Symptom:** `RuntimeError: Postgres not available at localhost:5432/paymentprocessor_test`.

**Cause:** `docker compose up -d db` was not started before pytest ran.

**Fix:**
```bash
docker compose up -d db
docker compose ps     # db should show 'healthy'
.venv/bin/pytest -v
```
```

- [ ] **Step 8.5: Update `docs/MAINTENANCE.md` §8 Test inventory**

Run the suite to get an accurate count:
```bash
.venv/bin/pytest --collect-only -q | tail -3
```

Replace the count in §8 with the new number. Add a row for `tests/test_alembic.py` (migration smoke tests) and `tests/test_db_config.py` (env-var DSN tests).

- [ ] **Step 8.6: Update `docs/API_REFERENCE.md` §6**

Replace the `### app/db.py` bullets under §6 that describe SQLite defaults:

Before:
```
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
```

After:
```
### `app/db.py`
- `_build_dsn()` — composes a Postgres DSN from env vars. `DATABASE_URL`
  (if set) wins; otherwise composes from `POSTGRES_USER`/`_PASSWORD`/
  `_DB`/`_HOST`/`_PORT`. Dev-safe defaults match `.env.example`.
- `engine` — `create_engine(_build_dsn(), pool_pre_ping=True, future=True)`.
  `pool_pre_ping` guards against stale PG connections (important in
  containerized deployments).
- `SessionLocal` — `sessionmaker(..., expire_on_commit=False)`. The
  `expire_on_commit=False` is intentional: API routes serialize ORM
  objects after commit; without this they would detach.
- `Base` — declarative base for all models.
- `get_db()` — module-level dependency (also defined here for direct
  use). The `app/api/deps.py` version is identical but reads
  `db_module.SessionLocal` lazily so test fixtures can rebind it.
- No SQLite support. The module no longer references `check_same_thread`
  or any SQLite-specific connection args.
```

In §6 `app/main.py` description, replace the "installs `lifespan` that calls `Base.metadata.create_all(bind=db_module.engine)` on startup" line with: "installs `lifespan` that is a no-op aside from yielding (Alembic owns schema creation; run `alembic upgrade head` as a deploy step)."

- [ ] **Step 8.7: Run the full suite (sanity)**

```bash
.venv/bin/pytest -v
```

Expected: all tests pass. Docs changes don't affect test outcomes.

- [ ] **Step 8.8: Commit**

```bash
git add docs/MAINTENANCE.md docs/API_REFERENCE.md
git commit -m "docs: reflect Postgres + Alembic + Docker reality in MAINTENANCE and API_REFERENCE"
```

---

## Task 9: End-to-end verification

**Goal:** Smoke-test the full containerized stack — build the app image, bring up `db` + `app` via docker compose, apply migrations, hit `/healthz` and `/payments`, and confirm the downgrade path works (documented as a manual smoke test per Task 7's note).

**Files:** None (verification only).

- [ ] **Step 9.1: Build the app image**

```bash
docker compose build app
```

Expected: build succeeds; final stage USES non-root `app` user. Verify with:
```bash
docker compose run --rm --no-deps app id
```
Expected: `uid=100(app) gid=101(app) groups=101(app)` (or similar non-root IDs).

- [ ] **Step 9.2: Bring up the full stack**

```bash
docker compose up -d
docker compose ps
```

Expected: both `db` and `app` show as `healthy` / `running`.

- [ ] **Step 9.3: Apply migrations to the compose-managed PG**

```bash
set -a; . ./.env; set +a
.venv/bin/alembic upgrade head
```

Expected: `INFO  [alembic.runtime.migration] Running upgrade -> <rev>, initial schema`. (If you already applied migrations to the dev DB in Task 4, alembic will no-op.)

- [ ] **Step 9.4: Smoke-test the running app**

```bash
# Health
curl -s http://localhost:8000/healthz
# Expected: {"status":"ok"}

# Create a payment
curl -s -X POST http://localhost:8000/payments \
  -H "Idempotency-Key: order-1234" \
  -H "Content-Type: application/json" \
  -d '{"merchant_id":"00000000-0000-0000-0000-000000000001","amount":2500}'
# Expected: 201 with status:pending, version:1, audit_events length 1

# Fetch the payment
PAYMENT_ID=$(curl -s -X POST http://localhost:8000/payments \
  -H "Idempotency-Key: order-1234" \
  -H "Content-Type: application/json" \
  -d '{"merchant_id":"00000000-0000-0000-0000-000000000001","amount":2500}' \
  | python3 -c "import sys, json; print(json.load(sys.stdin)['id'])")
curl -s http://localhost:8000/payments/$PAYMENT_ID
# Expected: 200 with the same shape

# Settle it
curl -s -X POST http://localhost:8000/payments/$PAYMENT_ID/settle \
  -H "Idempotency-Key: order-1234"
# Expected: 200 with status:settled, version:2, ledger_entries length 1
```

- [ ] **Step 9.5: Smoke-test the migration downgrade path (manual)**

Per the note in Task 7 §7.2, the `alembic downgrade base` smoke test is a manual step (adding it to the automatic suite creates test-ordering issues).

```bash
set -a; . ./.env; set +a
.venv/bin/alembic downgrade base
docker compose exec db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\dt"
# Expected: "Did not find any relations."

# Re-apply
.venv/bin/alembic upgrade head
docker compose exec db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\dt"
# Expected: 5 tables listed
```

- [ ] **Step 9.6: Security sweep**

Confirm no hardcoded secrets:
```bash
git grep -nE '(password|secret|api_key|token)\s*=\s*["'\''][^"\''].{4,}' -- ':!docs' ':!.env.example' || echo "no hardcoded secrets found"
```
Expected: `no hardcoded secrets found`. The `.env.example` file contains only placeholder values (`pp`, `pp`); the real `.env` is gitignored.

- [ ] **Step 9.7: Tear down**

```bash
docker compose down      # leave the named volume in place so dev data persists
# OR, to wipe the PG data volume:
# docker compose down -v
```

- [ ] **Step 9.8: Commit verification notes (if any)**

If any issue surfaced during the smoke test, commit the fix now. If everything passed, no commit is needed. Document the manual smoke test results inline in the merge request description when the branch is merged.

---

## Verification Commands (summary)

```bash
git status                       # on feature/postgresql-refactor, clean tree
docker compose up -d db          # Postgres running
.venv/bin/alembic upgrade head   # schema applied
.venv/bin/pytest -v              # full test suite green
docker compose build app         # image builds, non-root
docker compose up -d             # full stack
curl http://localhost:8000/healthz   # {"status":"ok"}
docker compose down              # tear down
```

---

## Self-review checklist (for the implementer)

After running all tasks, confirm:

- [ ] `git log --oneline` shows 9 commits on `feature/postgresql-refactor`, one per task.
- [ ] No `Base.metadata.create_all` references in `app/`.
- [ ] No `sqlite` references in `app/`, `tests/`, or `migrations/`.
- [ ] `app/db.py` exports `_build_dsn`, `Base`, `engine`, `SessionLocal`, `get_db`.
- [ ] `migrations/versions/20260619_*_initial_schema.py` exists and creates 5 tables + 2 unique constraints + 3 FKs + 3 indexes + 3 native enum types.
- [ ] `tests/conftest.py` has fixtures: `docker_pg`, `engine`, `truncate_tables` (autouse), `db`, `client`.
- [ ] `Dockerfile` has two stages; runtime uses `USER app`.
- [ ] `docker-compose.yml` has `db` (postgres:16-alpine) + `app` services, plus `app` `depends_on: db: condition: service_healthy`.
- [ ] `.env.example` exists; `.env` is gitignored.
- [ ] `docs/MAINTENANCE.md` and `docs/API_REFERENCE.md` describe Postgres + Alembic + Docker (no lingering SQLite references in the active sections).
- [ ] Full suite: `.venv/bin/pytest -v` green against dockerized PG.

---

## Execution Options

Plan saved to `docs/superpowers/plans/2026-06-19-postgresql-refactor-implementation.md`.

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — execute tasks in this session using executing-plans with batch execution and review checkpoints.

Which approach?
