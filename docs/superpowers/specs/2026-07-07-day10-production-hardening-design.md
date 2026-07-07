# Day 10 — Production Hardening Design

**Date:** 2026-07-07
**Scope:** paymentprocessor repo only
**Goal:** Move from "Day 9 security refactor deployed" to "automated, observable, and tamper-evident at the data layer". Five independent hardening workstreams, each independently shippable.

---

## Problem Statement

Day 9 closed with: NetworkPolicy enforced, RBAC least-privilege, SOPS secrets, image pinned, non-root containers. Three operational gaps remain:

1. **No automation** — every merge to `main` requires a human to manually bump the chart `tag:` field in `k8s/release.yaml` and `values-talos.yaml`. The existing GHCR workflow pushes `sha-<short>` tags to the registry, but Flux never sees the new tag.
2. **No observability** — the FastAPI app exposes no `/metrics` endpoint, emits no structured logs. There is no way to alert on error rate, latency, or payment-domain counts.
3. **Append-only enforced only by code contract** — `ledger_entries` and `audit_events` are INSERT-only by repository convention. A service bug, an ad-hoc query, or a compromised role could UPDATE or DELETE rows. For a bank-style platform, this must be enforced at the database level.
4. **No path for monitoring ingress** — once `/metrics` exists, NetworkPolicy must explicitly allow ingress from the monitoring namespace (which is not yet installed in the talos cluster).

---

## Architecture

Five workstreams, no shared runtime state, small DAG:

```
[1] CI tag-bump workflow              → enables auto-rollout on every merge
[2] /metrics Prometheus endpoint      → pairs with [5]
[3] structured JSON logging           → independent
[4] DB append-only triggers           → independent (Alembic migration)
[5] chart: monitoring ingress + SM    → pairs with [2], depends on networkpolicy.yaml patterns fixed in Day 9
```

Each produces its own tests and its own commit. Tasks 1-5 are independent → parallelizable.

---

## Components

### 1. CI auto-bump chart tag

**File:** `.github/workflows/bump-chart-tag.yml`

Triggered by `workflow_run` event when `Build and Push GHCR Image` completes successfully on `main`. Reads `head_sha` from the upstream workflow run, takes the 7-char short form, sed-replaces `tag:` in:
- `k8s/release.yaml`
- `payments-platform/values-talos.yaml`

Commits and pushes directly to `main` using `GITHUB_TOKEN` with `contents: write`. No PR (gated by green upstream build is sufficient). Serializes via `concurrency: group: bump-chart-tag`.

**Tradeoff considered:** chart-releaser / release-please with semver bumps — heavier than needed; SHA tag is the established convention.

### 2. /metrics Prometheus endpoint

**Files:**
- Modify: `app/main.py` — instantiate `Instrumentator().instrument(app).expose(app, endpoint="/metrics")`
- Modify: `app/services/payment.py` — increment custom counters on `create_payment` and `settle_payment`
- Modify: `requirements.txt` — add `prometheus-fastapi-instrumentator`
- Create: `tests/test_metrics.py`

Default instrumentator metrics: `http_requests_total`, `http_request_duration_seconds`, `http_requests_in_progress`. Custom counters:
- `payments_created_total{status="pending"}`
- `payments_settled_total{outcome="succeeded"|"idempotent_replay"|"conflict"}`

Tests assert: `GET /metrics` returns 200, content-type `text/plain; version=0.0.4`, body contains `http_requests_total` and `payments_created_total`.

**Tradeoff considered:** hand-rolled with `prometheus_client` — more code for same outcome; instrumentator is the FastAPI-idiomatic choice and auto-instruments HTTP metrics.

### 3. Structured JSON logging

**Files:**
- Create: `app/logging.py` — `structlog.configure(...)` with JSON renderer
- Modify: `app/main.py` — import and call `configure_logging()` in `create_app()`
- Modify: `requirements.txt` — add `structlog`
- Create: `tests/test_logging.py`

Processor pipeline:
```python
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)
```

Bridge uvicorn access logs through `structlog.stdlib.ProcessorFormatter` so a single JSON format covers app logs + access logs. `RequestIDMiddleware` binds `uuid4` per request to `structlog.contextvars`.

Never logs request bodies. Does not bind `merchant_id` or `amount` to log context (payment sensitivity).

Tests assert: log output parses as JSON, contains `request_id` field, does not contain `amount` or request body keys.

**Tradeoff considered:** stdlib `logging.Formatter` with JSON — works but loses processor-pipeline ergonomics; `structlog` gives clean redaction processor slot for future.

### 4. DB-level append-only enforcement

**Files:**
- Create: `migrations/versions/20260707_<sha>_append_only_triggers.py` — Alembic migration
- Create: `tests/test_append_only.py`

Migration creates a trigger function:
```sql
CREATE OR REPLACE FUNCTION pp_reject_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION '% is append-only; UPDATE/DELETE forbidden', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;
```

And per-table triggers:
```sql
CREATE TRIGGER tr_ledger_entries_append_only
    BEFORE UPDATE OR DELETE ON ledger_entries
    FOR EACH ROW EXECUTE FUNCTION pp_reject_mutation();

CREATE TRIGGER tr_audit_events_append_only
    BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION pp_reject_mutation();
```

Downgrade drops triggers and the function.

Tests assert: after `alembic upgrade head`, an `UPDATE` on `ledger_entries` raises `OperationalError` with the message; same for `DELETE`; same for `audit_events`.

**Tradeoff considered:** REVOKE UPDATE, DELETE from the app role — would require splitting the app role from the migrator role (CNPG uses a single `paymentprocessor` role today). Too invasive for the homelab; triggers preserve single-role setup with the same guarantee.

### 5. Chart: monitoring ingress + ServiceMonitor

**Files:**
- Modify: `payments-platform/values.yaml` — add `networkPolicy.ingressMonitoring` and `serviceMonitor` blocks
- Modify: `payments-platform/templates/networkpolicy.yaml` — emit additional ingress rule when `ingressMonitoring` selectors are non-empty
- Create: `payments-platform/templates/servicemonitor.yaml` — gated by `serviceMonitor.enabled`

Values schema additions:
```yaml
networkPolicy:
  ingressMonitoring:
    namespaceSelector: {}    # e.g. {kubernetes.io/metadata.name: monitoring}
    podSelector: {}          # e.g. {app.kubernetes.io/name: prometheus}

serviceMonitor:
  enabled: false
  namespace: monitoring
  selectorLabels:
    app.kubernetes.io/name: payments-platform
  interval: 30s
  scrapeTimeout: 10s
```

NetworkPolicy template emits an additional ingress rule (same pattern as the existing envoy-gateway rule, properly wrapped under `matchLabels:`) when `ingressMonitoring.namespaceSelector` and `podSelector` are both non-empty. Default `{}` = disabled.

`values-talos.yaml` stays empty for `ingressMonitoring` (no monitoring stack installed yet). `serviceMonitor.enabled` stays `false` in talos values.

Tests: extend `verify-networkpolicy.sh` to also assert that when `ingressMonitoring` selectors are set, the rendered NetworkPolicy contains a second ingress rule with the monitoring selectors preserved through apiserver normalization.

**Tradeoff considered:** emit the monitoring ingress unconditionally — would create a permissive rule when monitoring selectors are empty (empty `matchLabels` matches everything). Gating on non-empty selectors is the safe default.

---

## Security Properties After Refactor

| Property | Before | After |
|---|---|---|
| Image auto-rollout | Manual `tag:` bump required | CI bumps `tag:` automatically on every green build |
| Metrics | None | `/metrics` exposes HTTP + payment counters |
| Logging | Stdlib default (unstructured) | Structured JSON with `request_id` correlation |
| Append-only tables | Code contract only | DB triggers reject UPDATE/DELETE |
| Monitoring ingress | Not in chart | Template + values slot; disabled by default |

---

## Risks

1. **CI workflow_run race:** two merges in quick succession could both try to bump. Mitigated by `concurrency: group: bump-chart-tag, cancel-in-progress: false` (serialize, don't cancel mid-write).

2. **Migration failure mid-deploy:** Alembic job is a Helm pre-install/pre-upgrade hook; if the trigger migration fails, app upgrade fails. Mitigated by `--sql` dry-run + service-layer tests proving app still works (the app never UPDATE/DELETEs these tables).

3. **/metrics unauthenticated:** anyone with network access to port 8000 could read counters (no PII in default metrics). Mitigated by NetworkPolicy ingress rule restricting to monitoring namespace only — but only effective once component #5 is enabled with non-empty selectors, which today is not. Until then, `/metrics` is exposed to whatever ingress the chart allows (envoy-gateway). Acceptable for homelab; **do not enable in `payments.envoy.grendel71.net` production without first installing prometheus and enabling `ingressMonitoring` selectors.**

4. **structlog access-log duplication:** Uvicorn emits its own access logs; if we also bind route-level logging, we double-log. Mitigated in design: bridge via `ProcessorFormatter`; do NOT add per-route logging middleware (only `request_id` binding).

5. **Bump-tag direct-commit authenticity:** A direct push from CI does not pass human review. Mitigated by atomic, predictable pattern (1-line `tag:` replace); visible in git log; reversible by `git revert`.

---

## Out of Scope

- EKS production path (`infra/` stays as-is)
- Merchant table / currency column / refund flow / settlement API (domain-model evolution)
- CloudWatch / external alerting
- Talos monitoring stack installation (user's responsibility; chart provides the wiring)
