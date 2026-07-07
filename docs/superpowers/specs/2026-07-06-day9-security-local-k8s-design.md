# Day 9 — Security, Secrets & Local Kubernetes Design

**Date:** 2026-07-06  
**Scope:** paymentprocessor repo + talos repo  
**Goal:** Apply Day 9 best practices (secrets, RBAC, NetworkPolicy, namespace isolation) targeting the live talos homelab cluster, while structuring the paymentprocessor repo as the GitOps source of truth for all app-level manifests.

---

## Problem Statement

The paymentprocessor repo currently has:
- A production-grade Helm chart (`payments-platform/`) with security context, probes, PDB — but NetworkPolicy disabled
- An `infra/` directory of Terraform modules that only apply to AWS EKS — unused locally
- Empty `k8s/` and `charts/` directories
- No RBAC definitions
- No app-level k8s manifests at all — those live entirely in the talos repo

The talos repo currently:
- Deploys the app via `HelmRelease` pointing at the paymentprocessor repo's chart
- Has NetworkPolicy disabled in `release.yaml`
- Uses `tag: latest` with `pullPolicy: Always` — a security anti-pattern
- Owns all app deployment manifests (namespace, CNPG cluster, secrets, cert, httproute) as if it were the app repo

Day 9 requires: secure secret injection, least-privilege RBAC, non-root containers (already done), NetworkPolicy enforcement, and namespace isolation. The goal is also to restructure so the paymentprocessor repo is the authoritative source for all of these, with the talos repo providing only cluster-level GitOps wiring.

---

## Architecture

### Source of Truth Split

```
paymentprocessor repo (app source of truth)
├── payments-platform/          Helm chart — no changes to structure
│   ├── templates/
│   └── values-talos.yaml       NEW: talos-specific values (replaces inline values in talos release.yaml)
├── k8s/                        NEW: all app-level k8s manifests
│   ├── kustomization.yaml
│   ├── namespace.yaml
│   ├── rbac.yaml
│   ├── db.yaml                 CNPG Cluster (moved from talos repo)
│   ├── release.yaml            HelmRelease (moved from talos repo)
│   ├── secret.yaml             SOPS-encrypted (moved from talos repo)
│   ├── cert.yaml               cert-manager Certificate (moved from talos repo)
│   └── httproute.yaml          Envoy Gateway HTTPRoute (moved from talos repo)
└── infra/                      Unchanged — AWS EKS only, clearly labelled

talos repo (cluster wiring only)
└── k8s/flux/kustomizations/app-payments.yaml   MODIFIED: sourceRef → GitRepository/payment-processor
```

### GitOps Flow

```
paymentprocessor repo (GitHub)
        │
        │  Flux GitRepository source (already exists: payment-processor)
        ▼
Flux Kustomization (talos repo: app-payments.yaml)
  sourceRef: GitRepository/payment-processor
  path: ./k8s
  decryption: sops / sops-age secret
        │
        ▼
Applied to talos cluster:
  - Namespace: payments
  - RBAC: Role + RoleBinding (payments namespace)
  - CNPG Cluster: payments-db
  - Secret: payments-db-credentials (SOPS-decrypted)
  - Secret: paymentprocessor-db (SOPS-decrypted, DATABASE_URL)
  - HelmRelease: payments-platform → chart from same GitRepository
  - Certificate: payments-platform-tls
  - HTTPRoute: payments-platform-internal
```

The talos repo's `app-payments.yaml` Kustomization changes only its `path` and `sourceRef` — it already has SOPS decryption configured. No other changes to the talos repo's structure are needed.

---

## Components

### 1. `k8s/kustomization.yaml`
Standard Kustomize `Kustomization` listing all resources in the `k8s/` directory. This is what Flux's `Kustomization` CRD applies when it reconciles `path: ./k8s` from the paymentprocessor repo. Resource order: namespace → rbac → secret → db → release → cert → httproute.

### 2. `k8s/namespace.yaml`
Defines the `payments` Namespace. Moving from talos repo to paymentprocessor repo makes namespace definition co-located with the app that uses it.

### 3. `k8s/rbac.yaml`
Defines a `Role` and `RoleBinding` in the `payments` namespace.

### 4. `k8s/db.yaml`
CNPG `Cluster` resource for `payments-db`. Moved from the talos repo verbatim. Stored here so the database definition is versioned with the app that owns it.

### 5. `k8s/secret.yaml`
Two SOPS-encrypted secrets:
- `payments-db-credentials`: CNPG bootstrap credentials (username/password)
- `paymentprocessor-db`: DATABASE_URL consumed by the Helm chart

These are encrypted with the same age recipient already in the talos repo (`age1jc2yh5yr5xdmgfu46q42z3pxy0aztn94x5hsarzmjwxzt9f4tq3qtsmrup`). Safe to store in the paymentprocessor repo — the ciphertext is public; only the age private key (in the talos cluster's `sops-age` secret) can decrypt.

### 6. `k8s/release.yaml`
The `HelmRelease` for `payments-platform`. Values currently inlined in the talos repo's `release.yaml` are extracted to `payments-platform/values-talos.yaml` and referenced via `valuesFrom`. Key security fix: `tag: latest` → pinned tag, `pullPolicy: Always` → `IfNotPresent`.

### 7. `k8s/cert.yaml` and `k8s/httproute.yaml`
Moved from the talos repo verbatim. These are app-level routing concerns, not cluster infrastructure.

### 8. `payments-platform/values-talos.yaml`
New values file containing the talos-specific Helm values that were previously scattered inside the talos repo's `release.yaml`. This makes the deployment configuration reviewable from the app repo.

Key values:
```yaml
networkPolicy:
  enabled: true
  ingress:
    namespaceSelector:
      kubernetes.io/metadata.name: envoy-gateway-system
    podSelector:
      app.kubernetes.io/name: envoy-gateway
  egress:
    dnsNamespaceSelector:
      kubernetes.io/metadata.name: kube-system
    dnsPodSelector:
      k8s-app: kube-dns
    database:
      podSelector:                    # in-cluster CNPG — use podSelector not cidr
        cnpg.io/cluster: payments-db
      namespaceSelector:
        kubernetes.io/metadata.name: payments
      port: 5432
```

### 9. NetworkPolicy fix: in-cluster database egress
The existing `networkpolicy.yaml` template uses `ipBlock.cidr` for database egress. This only works for external databases. CNPG runs in-cluster (`payments` namespace). The template needs a new egress rule variant: `podSelector` + `namespaceSelector` targeting the CNPG pods, instead of (or in addition to) `ipBlock`.

### 10. Migration Job ServiceAccount fix
`migration-job.yaml` currently hardcodes `serviceAccountName: default`. This bypasses the chart's dedicated ServiceAccount and any RBAC attached to it. Fix: use `{{ include "payments-platform.serviceAccountName" . }}`.

### 11. `infra/` directory marker
Add `infra/README.md` clearly stating this is AWS/EKS infrastructure only, not used for local Kubernetes deployment.

---

## Security Properties After Refactor

| Property | Before | After |
|---|---|---|
| NetworkPolicy | Disabled | Enabled — ingress from Envoy Gateway only, egress to DNS + CNPG only |
| RBAC | None | Role in payments namespace, least privilege |
| Secret storage | In talos repo only | SOPS-encrypted in paymentprocessor repo |
| Image tag | `latest` + `Always` pull | Pinned tag + `IfNotPresent` |
| Migration SA | `default` SA | Dedicated chart SA |
| DB egress rule | `ipBlock` (wrong for CNPG) | `podSelector` (correct for in-cluster) |
| Source of truth | Split across two repos | App manifests in paymentprocessor repo |

---

## What Does NOT Change

- The talos repo's SOPS age key and `sops-age` cluster secret — unchanged
- The `payment-processor` GitRepository source in the talos repo — already exists, already watches the right branch
- The Helm chart template structure — only `networkpolicy.yaml` and `migration-job.yaml` get targeted fixes
- The `infra/` Terraform modules — left in place, marked cloud-only
- The talos repo's other `k8s/apps/payments/` files are removed once their equivalents are in the paymentprocessor repo (as part of the plan, not this spec)

---

## Risks

1. **SOPS key access**: Encrypting new secrets in the paymentprocessor repo requires the age private key locally. If the developer doesn't have it, secrets can't be re-encrypted. Mitigation: document the key requirement; existing secrets can be copied verbatim from the talos repo since the ciphertext is identical.

2. **NetworkPolicy misconfiguration**: Enabling NetworkPolicy with wrong selectors will silently drop traffic to the app. Mitigation: test with `kubectl exec` curl before and after; use `networkPolicy.enabled: false` as rollback in the HelmRelease.

3. **Flux dependency ordering**: The `HelmRelease` depends on the `payments-db` CNPG cluster being ready, which depends on the namespace existing. Flux `dependsOn` in the Kustomization handles this at the Kustomization level; within the `k8s/` directory, resource ordering is handled by Flux's server-side apply — Namespace and RBAC apply before HelmRelease by resource type priority.

4. **talos repo cleanup**: Once paymentprocessor repo's `k8s/` is wired up, the old `k8s/apps/payments/` files in the talos repo become redundant and must be removed to avoid Flux applying duplicate resources. This is a sequenced operation: wire new source first, verify cluster health, then remove old manifests.

5. **Image tag pinning**: Changing from `latest` to a pinned tag means the cluster won't auto-update on push. A CI step (GitHub Actions) should update the tag in `k8s/release.yaml` on each merge to main. This is a Day 10 concern; for Day 9, pin to the current known-good tag.

---

## Out of Scope

- GitHub Actions CI pipeline (Day 10)
- Prometheus metrics endpoint
- CloudWatch / external monitoring
- AWS Secrets Manager integration
- EKS deployment path (infra/ remains unchanged)
- Cluster-level Flux bootstrap (already done in talos repo)
