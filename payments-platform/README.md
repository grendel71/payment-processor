# Payments Platform Helm Chart

## Architecture

This chart deploys only the FastAPI payment processor application. PostgreSQL is external to the chart and is provided through an existing Kubernetes Secret containing `DATABASE_URL`.

The application exposes `/healthz` for liveness checks and `/readyz` for readiness checks. `/healthz` verifies the process is alive, while `/readyz` verifies the application can reach the database before Kubernetes routes traffic to the pod.

## Required Secret

Create the database Secret before installing the chart. Do not commit real credentials to source control.

```sh
kubectl create secret generic paymentprocessor-db \
  --namespace payments \
  --from-literal=DATABASE_URL='postgresql+psycopg2://user:password@postgres.example.com:5432/paymentprocessor'
```

## Install

```sh
helm upgrade --install payments-platform ./payments-platform \
  --namespace payments \
  --create-namespace \
  -f payments-platform/values-prod.yaml \
  --set database.existingSecret.name=paymentprocessor-db \
  --set image.repository=registry.example.com/payments/paymentprocessor \
  --set image.tag=0.1.0
```

## Migrations

When `migrations.enabled` is `true`, Helm runs a pre-install/pre-upgrade Job that executes:

```sh
alembic upgrade head
```

Disable the Helm migration Job when another deployment step already runs Alembic:

```sh
helm upgrade --install payments-platform ./payments-platform \
  --namespace payments \
  -f payments-platform/values-prod.yaml \
  --set migrations.enabled=false
```

Helm rollback does not roll back database schema changes. Keep migrations backward-compatible with the previously deployed application version.

## Security Defaults

- ServiceAccount token automount is disabled.
- Containers run as non-root users.
- Runtime seccomp profile is enabled.
- Linux capabilities are dropped.
- Privilege escalation is disabled.
- Resource requests and limits are configured.
- Database credentials come from an existing Kubernetes Secret.
- NetworkPolicy is optional and can restrict ingress and egress.

## NetworkPolicy

Production values must replace the example ingress selectors, DNS selectors, and database CIDR with cluster-specific least-privilege values. The sample values are placeholders and should not be used as-is for production access control.

When NetworkPolicy is enabled, Helm test pods must be allowed by policy or `helm test` can be denied. Disable NetworkPolicy for tests or add ingress selectors for Helm test pods.

## Verification

```sh
helm lint ./payments-platform -f payments-platform/values-prod.yaml
helm template payments-platform ./payments-platform -f payments-platform/values-prod.yaml
helm test payments-platform -n payments
```

## Important Values

| Value | Description |
| --- | --- |
| `database.existingSecret.name` | Name of the existing Secret containing the database URL. |
| `database.existingSecret.key` | Key in the existing Secret that stores `DATABASE_URL`. |
| `migrations.enabled` | Enables the Helm pre-install/pre-upgrade Alembic migration Job. |
| `autoscaling.enabled` | Enables the HorizontalPodAutoscaler. |
| `podDisruptionBudget.enabled` | Enables the PodDisruptionBudget for availability during voluntary disruptions. |
| `networkPolicy.enabled` | Enables NetworkPolicy rules for app ingress and egress. |
