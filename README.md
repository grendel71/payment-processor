# Payment Processor

A bank-style payments platform built with FastAPI, SQLAlchemy, PostgreSQL, and deployed on AWS EKS via Terraform.

---

## What it does

- **Create payments** — records a `pending` payment for a merchant with mandatory idempotency
- **Settle payments** — transitions `pending → settled`, writing an immutable ledger debit entry
- **Audit trail** — every state change produces an append-only `AuditEvent`
- **Concurrency safety** — optimistic versioning prevents double-settlement

State machine: `pending → settled` (success) or `pending → failed` (future). Settling a `failed` payment returns `409`.

---

## Project layout

```
app/
  api/          # Thin HTTP routes (FastAPI routers)
  services/     # Domain logic — PaymentService owns the state machine
  repositories/ # SQLAlchemy data access, one per aggregate
  models/       # ORM models: Payment, LedgerEntry, AuditEvent
  schemas/      # Pydantic request/response shapes
  db.py         # Engine, session factory, Base

migrations/     # Alembic migration scripts
tests/          # Pytest integration tests (real Postgres via SQLite fallback)

infra/
  bootstrap/    # Terraform: S3 state bucket + DynamoDB lock table
  modules/      # vpc, iam, eks, ecr Terraform modules
  environments/ # Per-environment composer (prod)

charts/         # Helm charts (placeholder for Kubernetes deploy)
docs/           # API_REFERENCE.md, design specs
```

---

## Local development

**Prerequisites:** Python 3.11+, Docker, Docker Compose.

```bash
# 1. Copy env vars
cp .env.example .env

# 2. Start Postgres
docker compose up db -d

# 3. Create and activate a virtualenv
python -m venv .venv && source .venv/bin/activate

# 4. Install dependencies
pip install -r requirements-dev.txt

# 5. Run migrations
alembic upgrade head

# 6. Start the API
uvicorn app.main:app --reload
```

API available at `http://localhost:8000`. Interactive docs at `/docs`.

---

## Running tests

```bash
pytest
```

Tests spin up a real Postgres session (configured via the same env vars) and run migrations automatically through the `engine` fixture in `tests/conftest.py`.

---

## Key API endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/healthz` | Liveness probe |
| `POST` | `/payments` | Create payment (requires `Idempotency-Key` header) |
| `GET` | `/payments/{id}` | Fetch payment with ledger and audit events |
| `POST` | `/payments/{id}/settle` | Settle a pending payment (requires `Idempotency-Key` header) |

See `docs/API_REFERENCE.md` for full request/response schemas and error codes.

---

## Infrastructure

Terraform modules under `infra/` provision:

- **VPC** — public/private subnets, NAT gateway
- **EKS** — cluster, node group, OIDC provider, IMDSv2 hardening
- **IAM** — cluster, node, IRSA, and deploy roles (least-privilege)
- **ECR** — private image registry with scan-on-push and lifecycle policy

Bootstrap remote state first:

```bash
cd infra/bootstrap && terraform init && terraform apply
cd infra/environments/prod && terraform init && terraform apply
```

No manual cloud resources. All infrastructure is code.

---

## Architecture decisions

- Routes are thin — domain rules live exclusively in `PaymentService`
- `session.commit()` is owned by the API layer; the service never commits
- Ledger and audit tables are append-only by contract (no UPDATE/DELETE)
- Idempotency is scoped to `(merchant_id, idempotency_key)` with a DB unique constraint
- Schema migrations are managed by Alembic; the app does not auto-create tables at boot
