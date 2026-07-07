# Day 9 — Security, Secrets & Local Kubernetes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the paymentprocessor repo the GitOps source of truth for all app-level Kubernetes manifests, applying Day 9 security best practices (RBAC, NetworkPolicy, SOPS secrets, image pinning) against the live talos homelab cluster.

**Architecture:** All app-level manifests move from `talos/k8s/apps/payments/` into `paymentprocessor/k8s/`. Flux on the talos cluster already watches the paymentprocessor repo via a `GitRepository` source; the talos repo's `app-payments.yaml` Kustomization is updated to point `path: ./k8s` at that source instead of its own tree. SOPS decryption is already configured on the talos cluster — secrets encrypted with the existing age key work immediately.

**Tech Stack:** Kubernetes, Flux CD v2, Helm, SOPS/age, CloudNative-PG, Envoy Gateway, cert-manager, Kustomize

---

## File Map

### paymentprocessor repo — files created
- `k8s/kustomization.yaml` — Kustomize resource list, controls apply order
- `k8s/namespace.yaml` — `payments` Namespace
- `k8s/rbac.yaml` — Role + RoleBinding (least-privilege, payments namespace)
- `k8s/secret.yaml` — SOPS-encrypted: `payments-db-credentials` + `paymentprocessor-db`
- `k8s/db.yaml` — CNPG Cluster `payments-db`
- `k8s/release.yaml` — HelmRelease `payments-platform`
- `k8s/cert.yaml` — cert-manager Certificate
- `k8s/httproute.yaml` — Envoy Gateway HTTPRoute
- `payments-platform/values-talos.yaml` — talos-specific Helm values
- `infra/README.md` — cloud-only marker

### paymentprocessor repo — files modified
- `payments-platform/templates/networkpolicy.yaml` — add in-cluster DB egress via podSelector
- `payments-platform/templates/migration-job.yaml` — use chart ServiceAccount instead of `default`
- `payments-platform/values.yaml` — add `networkPolicy.egress.database.podSelector` + `namespaceSelector` fields

### talos repo — files modified
- `k8s/flux/kustomizations/app-payments.yaml` — point sourceRef at GitRepository/payment-processor, path ./k8s

### talos repo — files deleted (after cutover verified)
- `k8s/apps/payments/kustomization.yaml`
- `k8s/apps/payments/namespace.yaml` (if present — currently namespace is in `infra/namespace/payments.yaml`, leave that)
- `k8s/apps/payments/release.yaml`
- `k8s/apps/payments/secret.yaml`
- `k8s/apps/payments/db.yaml`
- `k8s/apps/payments/cert.yaml`
- `k8s/apps/payments/httproute.yaml`
- `k8s/apps/payments/source.yaml` — GitRepository moves to talos flux-system permanently (keep it, it's used by the new Kustomization too)

---

## Task 1: Add Helm chart values schema for in-cluster DB NetworkPolicy

The `values.yaml` currently only supports `networkPolicy.egress.database.cidr` (for external DBs). CNPG is in-cluster so we need a `podSelector`/`namespaceSelector` variant. Add the new fields to `values.yaml` and update `networkpolicy.yaml` to emit the correct egress rule.

**Files:**
- Modify: `payments-platform/values.yaml`
- Modify: `payments-platform/templates/networkpolicy.yaml`

- [ ] **Step 1: Add new database egress fields to values.yaml**

In `payments-platform/values.yaml`, replace the `database:` block under `networkPolicy.egress` (currently lines 134–136):

```yaml
    database:
      cidr: ""
      port: 5432
```

with:

```yaml
    database:
      cidr: ""
      podSelector: {}
      namespaceSelector: {}
      port: 5432
```

- [ ] **Step 2: Update networkpolicy.yaml to support podSelector-based DB egress**

In `payments-platform/templates/networkpolicy.yaml`, replace the database egress block that currently reads:

```yaml
    {{- if .Values.networkPolicy.egress.database.cidr }}
    - to:
        - ipBlock:
            cidr: {{ .Values.networkPolicy.egress.database.cidr | quote }}
      ports:
        - protocol: TCP
          port: {{ .Values.networkPolicy.egress.database.port }}
    {{- end }}
```

with (appears twice — once for the app NetworkPolicy, once for the migration job NetworkPolicy — replace **both**):

```yaml
    {{- $dbCidr := .Values.networkPolicy.egress.database.cidr }}
    {{- $dbPodSelector := .Values.networkPolicy.egress.database.podSelector }}
    {{- $dbNsSelector := .Values.networkPolicy.egress.database.namespaceSelector }}
    {{- if $dbCidr }}
    - to:
        - ipBlock:
            cidr: {{ $dbCidr | quote }}
      ports:
        - protocol: TCP
          port: {{ .Values.networkPolicy.egress.database.port }}
    {{- else if and (kindIs "map" $dbPodSelector) (gt (len $dbPodSelector) 0) }}
    - to:
        - namespaceSelector:
            {{- toYaml $dbNsSelector | nindent 12 }}
          podSelector:
            {{- toYaml $dbPodSelector | nindent 12 }}
      ports:
        - protocol: TCP
          port: {{ .Values.networkPolicy.egress.database.port }}
    {{- end }}
```

- [ ] **Step 3: Verify helm template renders without error**

```bash
cd /home/blau/paymentprocessor
helm template test-release payments-platform \
  --set "database.existingSecret.name=test-secret" \
  --set "database.existingSecret.key=DATABASE_URL" \
  --set "networkPolicy.enabled=true" \
  --set "networkPolicy.ingress.namespaceSelector.kubernetes\\.io/metadata\\.name=envoy-gateway-system" \
  --set "networkPolicy.ingress.podSelector.app\\.kubernetes\\.io/name=envoy-gateway" \
  --set "networkPolicy.egress.dnsNamespaceSelector.kubernetes\\.io/metadata\\.name=kube-system" \
  --set "networkPolicy.egress.dnsPodSelector.k8s-app=kube-dns" \
  --set "networkPolicy.egress.database.podSelector.cnpg\\.io/cluster=payments-db" \
  --set "networkPolicy.egress.database.namespaceSelector.kubernetes\\.io/metadata\\.name=payments" \
  2>&1 | grep -E "^(Error|---)" | head -20
```

Expected: lines beginning with `---` only, no `Error:` lines.

- [ ] **Step 4: Commit**

```bash
cd /home/blau/paymentprocessor
git add payments-platform/values.yaml payments-platform/templates/networkpolicy.yaml
git commit -m "fix(helm): add podSelector-based DB egress for in-cluster CNPG"
```

---

## Task 2: Fix migration job ServiceAccount

`migration-job.yaml` hardcodes `serviceAccountName: default`. This bypasses the chart's dedicated ServiceAccount and any RBAC bound to it.

**Files:**
- Modify: `payments-platform/templates/migration-job.yaml`

- [ ] **Step 1: Replace hardcoded default SA**

In `payments-platform/templates/migration-job.yaml`, find and replace:

```yaml
      serviceAccountName: default
```

with:

```yaml
      serviceAccountName: {{ include "payments-platform.serviceAccountName" . }}
```

- [ ] **Step 2: Verify helm template renders the SA correctly**

```bash
cd /home/blau/paymentprocessor
helm template test-release payments-platform \
  --set "database.existingSecret.name=test-secret" \
  --set "database.existingSecret.key=DATABASE_URL" \
  --set "migrations.enabled=true" \
  2>&1 | grep "serviceAccountName"
```

Expected output contains `serviceAccountName: test-release-payments-platform` (not `default`).

- [ ] **Step 3: Commit**

```bash
cd /home/blau/paymentprocessor
git add payments-platform/templates/migration-job.yaml
git commit -m "fix(helm): migration job uses chart ServiceAccount instead of default"
```

---

## Task 3: Create values-talos.yaml

Extract the talos-specific Helm values from the talos repo's `release.yaml` into a new `payments-platform/values-talos.yaml`. This makes the production deployment config reviewable from the app repo.

**Files:**
- Create: `payments-platform/values-talos.yaml`

- [ ] **Step 1: Create values-talos.yaml**

Create `/home/blau/paymentprocessor/payments-platform/values-talos.yaml` with:

```yaml
replicaCount: 2

image:
  repository: ghcr.io/grendel71/payment-processor
  pullPolicy: IfNotPresent
  tag: latest  # pinned to sha tag in Task 6

database:
  existingSecret:
    name: paymentprocessor-db
    key: DATABASE_URL

migrations:
  enabled: true

podDisruptionBudget:
  enabled: true
  minAvailable: 1

autoscaling:
  enabled: false

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
      podSelector:
        cnpg.io/cluster: payments-db
      namespaceSelector:
        kubernetes.io/metadata.name: payments
      port: 5432
```

- [ ] **Step 2: Verify helm template renders NetworkPolicy with podSelector DB egress**

```bash
cd /home/blau/paymentprocessor
helm template payments-platform payments-platform \
  -f payments-platform/values-talos.yaml \
  2>&1 | grep -A 20 "kind: NetworkPolicy" | head -40
```

Expected: NetworkPolicy with `cnpg.io/cluster: payments-db` in the egress podSelector, and `envoy-gateway-system` in the ingress namespaceSelector.

- [ ] **Step 3: Commit**

```bash
cd /home/blau/paymentprocessor
git add payments-platform/values-talos.yaml
git commit -m "feat(helm): add values-talos.yaml with NetworkPolicy and CNPG egress"
```

---

## Task 4: Create k8s/namespace.yaml and k8s/rbac.yaml

Establish the `payments` namespace definition and least-privilege RBAC in the paymentprocessor repo.

**Files:**
- Create: `k8s/namespace.yaml`
- Create: `k8s/rbac.yaml`

- [ ] **Step 1: Create k8s/namespace.yaml**

Create `/home/blau/paymentprocessor/k8s/namespace.yaml`:

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: payments
```

- [ ] **Step 2: Create k8s/rbac.yaml**

Create `/home/blau/paymentprocessor/k8s/rbac.yaml`:

```yaml
---
# Least-privilege Role for the payments-platform ServiceAccount.
# Grants only get on Secrets within the payments namespace —
# sufficient for the app to read its own injected credentials.
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: payments-platform
  namespace: payments
rules:
  - apiGroups: [""]
    resources: ["secrets"]
    verbs: ["get"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: payments-platform
  namespace: payments
subjects:
  - kind: ServiceAccount
    name: payments-platform
    namespace: payments
roleRef:
  kind: Role
  name: payments-platform
  apiGroup: rbac.authorization.k8s.io
```

- [ ] **Step 3: Validate YAML is well-formed**

```bash
kubectl apply --dry-run=client -f /home/blau/paymentprocessor/k8s/namespace.yaml
kubectl apply --dry-run=client -f /home/blau/paymentprocessor/k8s/rbac.yaml
```

Expected: `namespace/payments configured (dry run)` and `role.rbac.../payments configured (dry run)` + `rolebinding.../payments configured (dry run)`.

- [ ] **Step 4: Commit**

```bash
cd /home/blau/paymentprocessor
git add k8s/namespace.yaml k8s/rbac.yaml
git commit -m "feat(k8s): add payments namespace and least-privilege RBAC"
```

---

## Task 5: Copy SOPS-encrypted secrets into k8s/secret.yaml

Copy the encrypted secret file verbatim from the talos repo. The ciphertext is identical — same age key recipient, same plaintext — so no re-encryption is needed.

**Files:**
- Create: `k8s/secret.yaml`

- [ ] **Step 1: Copy secret.yaml from talos repo**

```bash
cp /home/blau/talos/k8s/apps/payments/secret.yaml \
   /home/blau/paymentprocessor/k8s/secret.yaml
```

- [ ] **Step 2: Verify the file contains SOPS ciphertext and correct structure**

```bash
head -10 /home/blau/paymentprocessor/k8s/secret.yaml
```

Expected: file begins with `apiVersion: ENC[AES256_GCM` and contains `sops:` block with the homelab age recipient `age1jc2yh5yr5xdmgfu46q42z3pxy0aztn94x5hsarzmjwxzt9f4tq3qtsmrup`.

- [ ] **Step 3: Commit**

```bash
cd /home/blau/paymentprocessor
git add k8s/secret.yaml
git commit -m "feat(k8s): add SOPS-encrypted payments secrets"
```

---

## Task 6: Create k8s/db.yaml and pin image tag in values-talos.yaml

Move the CNPG Cluster definition into the paymentprocessor repo and pin the image tag to the most recent short-SHA pushed by CI.

**Files:**
- Create: `k8s/db.yaml`
- Modify: `payments-platform/values-talos.yaml`

- [ ] **Step 1: Copy db.yaml from talos repo**

```bash
cp /home/blau/talos/k8s/apps/payments/db.yaml \
   /home/blau/paymentprocessor/k8s/db.yaml
```

- [ ] **Step 2: Verify db.yaml content**

```bash
cat /home/blau/paymentprocessor/k8s/db.yaml
```

Expected: contains `kind: Cluster`, `name: payments-db`, `database: paymentprocessor`, `secret.name: payments-db-credentials`.

- [ ] **Step 3: Find the most recent SHA tag pushed to GHCR**

```bash
git -C /home/blau/paymentprocessor log --oneline -1 main 2>/dev/null || \
git -C /home/blau/paymentprocessor log --oneline -1
```

Note the 7-character short SHA from the output (e.g. `9e8afcc`). The GHCR tag format from the CI workflow is `sha-<shortsha>`.

- [ ] **Step 4: Pin image tag in values-talos.yaml**

In `payments-platform/values-talos.yaml`, replace:

```yaml
  tag: latest  # pinned to sha tag in Task 6
```

with the actual short SHA tag (replace `SHA` with the 7-char value from Step 3):

```yaml
  tag: "sha-SHA"
```

For example, if the short SHA is `9e8afcc`:

```yaml
  tag: "sha-9e8afcc"
```

- [ ] **Step 5: Commit**

```bash
cd /home/blau/paymentprocessor
git add k8s/db.yaml payments-platform/values-talos.yaml
git commit -m "feat(k8s): add CNPG cluster definition and pin image tag"
```

---

## Task 7: Create k8s/release.yaml, k8s/cert.yaml, k8s/httproute.yaml

The HelmRelease now references `values-talos.yaml` via `valuesFrom`. Copy cert and httproute verbatim from the talos repo.

**Files:**
- Create: `k8s/release.yaml`
- Create: `k8s/cert.yaml`
- Create: `k8s/httproute.yaml`

- [ ] **Step 1: Create k8s/release.yaml**

Create `/home/blau/paymentprocessor/k8s/release.yaml`:

```yaml
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: payments-platform
  namespace: payments
spec:
  interval: 30m
  chart:
    spec:
      chart: ./payments-platform
      sourceRef:
        kind: GitRepository
        name: payment-processor
        namespace: flux-system
      interval: 5m
  install:
    remediation:
      retries: 3
  upgrade:
    remediation:
      retries: 3
  valuesFrom:
    - kind: GitRepository
      name: payment-processor
      namespace: flux-system
      valuesKey: payments-platform/values-talos.yaml
```

- [ ] **Step 2: Copy cert.yaml and httproute.yaml from talos repo**

```bash
cp /home/blau/talos/k8s/apps/payments/cert.yaml \
   /home/blau/paymentprocessor/k8s/cert.yaml

cp /home/blau/talos/k8s/apps/payments/httproute.yaml \
   /home/blau/paymentprocessor/k8s/httproute.yaml
```

- [ ] **Step 3: Verify files copied correctly**

```bash
cat /home/blau/paymentprocessor/k8s/cert.yaml
cat /home/blau/paymentprocessor/k8s/httproute.yaml
```

Expected: `cert.yaml` contains `kind: Certificate`, `issuerRef.name: letsencrypt-production`, `dnsNames: payments.envoy.grendel71.net`. `httproute.yaml` contains `kind: HTTPRoute`, `parentRefs` pointing at `envoy-external`, hostname `papi.grendel71.net`.

- [ ] **Step 4: Commit**

```bash
cd /home/blau/paymentprocessor
git add k8s/release.yaml k8s/cert.yaml k8s/httproute.yaml
git commit -m "feat(k8s): add HelmRelease, Certificate, and HTTPRoute manifests"
```

---

## Task 8: Create k8s/kustomization.yaml

The Kustomize `Kustomization` resource list is what Flux applies when it reconciles `path: ./k8s`. Resource order matters: Namespace before everything, Secrets before CNPG (bootstrap secret), CNPG before HelmRelease.

**Files:**
- Create: `k8s/kustomization.yaml`

- [ ] **Step 1: Create k8s/kustomization.yaml**

Create `/home/blau/paymentprocessor/k8s/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - namespace.yaml
  - rbac.yaml
  - secret.yaml
  - db.yaml
  - release.yaml
  - cert.yaml
  - httproute.yaml
```

- [ ] **Step 2: Validate kustomization builds without error**

```bash
kubectl kustomize /home/blau/paymentprocessor/k8s/ 2>&1 | grep -c "kind:"
```

Expected: outputs `7` (7 resource kinds). If `kubectl kustomize` is unavailable, use `kustomize build /home/blau/paymentprocessor/k8s/`.

- [ ] **Step 3: Commit**

```bash
cd /home/blau/paymentprocessor
git add k8s/kustomization.yaml
git commit -m "feat(k8s): add Kustomization resource list"
```

---

## Task 9: Add .sops.yaml to paymentprocessor repo

SOPS needs a config file in the paymentprocessor repo so it knows which age key to use when encrypting future secrets.

**Files:**
- Create: `.sops.yaml`

- [ ] **Step 1: Create .sops.yaml**

Create `/home/blau/paymentprocessor/.sops.yaml`:

```yaml
keys:
  - &homelab age1jc2yh5yr5xdmgfu46q42z3pxy0aztn94x5hsarzmjwxzt9f4tq3qtsmrup

creation_rules:
  - path_regex: k8s/secret.*\.yaml$
    encrypted_regex: "^(data|stringData)$"
    age: *homelab
```

- [ ] **Step 2: Verify sops can identify the creation rule for k8s/secret.yaml**

```bash
sops --config /home/blau/paymentprocessor/.sops.yaml \
  --encrypt --dry-run \
  /home/blau/paymentprocessor/k8s/secret.yaml 2>&1 | head -5
```

Expected: either outputs encrypted content or a message showing the age recipient is matched. An error about missing private key is acceptable — that means the config is correct but the key isn't available locally (the cluster holds it).

- [ ] **Step 3: Commit**

```bash
cd /home/blau/paymentprocessor
git add .sops.yaml
git commit -m "chore: add SOPS config for k8s secrets encryption"
```

---

## Task 10: Add infra/README.md cloud-only marker

**Files:**
- Create: `infra/README.md`

- [ ] **Step 1: Create infra/README.md**

Create `/home/blau/paymentprocessor/infra/README.md`:

```markdown
# Infrastructure — AWS / EKS Only

The Terraform modules and environments in this directory provision AWS cloud infrastructure:

- `modules/vpc/` — VPC, public/private subnets, NAT gateway
- `modules/eks/` — EKS cluster, node group, OIDC provider
- `modules/iam/` — Cluster, node, IRSA, and deploy roles
- `modules/ecr/` — Private image registry
- `environments/prod/` — Production environment composer

**These are not used for local Kubernetes deployment.**

For local Kubernetes (Talos homelab), all manifests live in `../k8s/` and are
managed by FluxCD. See `../k8s/kustomization.yaml` for the app resource list.
```

- [ ] **Step 2: Commit**

```bash
cd /home/blau/paymentprocessor
git add infra/README.md
git commit -m "docs(infra): mark directory as AWS/EKS cloud-only"
```

---

## Task 11: Update talos repo — rewire app-payments Kustomization

This is the cutover step. Update the Flux Kustomization in the talos repo to point at the paymentprocessor repo's `k8s/` directory instead of its own `k8s/apps/payments/` path.

**Files:**
- Modify: `k8s/flux/kustomizations/app-payments.yaml` (talos repo)

- [ ] **Step 1: Read the current app-payments.yaml**

```bash
cat /home/blau/talos/k8s/flux/kustomizations/app-payments.yaml
```

Expected: `sourceRef.name: flux-system`, `path: ./k8s/apps/payments`.

- [ ] **Step 2: Update app-payments.yaml**

Replace the entire content of `/home/blau/talos/k8s/flux/kustomizations/app-payments.yaml` with:

```yaml
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: payments
  namespace: flux-system
spec:
  interval: 10m
  dependsOn:
    - name: namespace
    - name: cnpg
  sourceRef:
    kind: GitRepository
    name: payment-processor
    namespace: flux-system
  path: ./k8s
  prune: true
  timeout: 5m
  wait: true
  decryption:
    provider: sops
    secretRef:
      name: sops-age
```

Key changes from the original:
- `sourceRef.name` changed from `flux-system` to `payment-processor`
- `path` changed from `./k8s/apps/payments` to `./k8s`
- Added `dependsOn: cnpg` so CNPG operator is ready before the Cluster resource applies

- [ ] **Step 3: Verify the cnpg Kustomization name**

```bash
grep "name:" /home/blau/talos/k8s/flux/kustomizations/cnpg.yaml | head -5
```

Expected: confirms the CNPG Kustomization is named `cnpg`. If it has a different name, update the `dependsOn` entry in Step 2 accordingly.

- [ ] **Step 4: Commit to talos repo**

```bash
cd /home/blau/talos
git add k8s/flux/kustomizations/app-payments.yaml
git commit -m "feat(flux): rewire payments Kustomization to paymentprocessor repo k8s/"
```

---

## Task 12: Remove redundant manifests from talos repo

Once the paymentprocessor repo's `k8s/` is the active source, the old talos app manifests become dead code. Remove them to prevent Flux applying duplicate resources.

**Important:** Do this only after Task 11 is committed and pushed, and after verifying the cluster is healthy with the new source (see verification step below).

**Files:**
- Delete from talos repo: `k8s/apps/payments/release.yaml`, `secret.yaml`, `db.yaml`, `cert.yaml`, `httproute.yaml`, `kustomization.yaml`
- Keep: `k8s/apps/payments/source.yaml` — the `GitRepository` for `payment-processor` is still needed by Flux

- [ ] **Step 1: Push Task 11 commit and verify Flux reconciles**

```bash
cd /home/blau/talos
git push

# Wait ~2 minutes then check Flux status
kubectl -n flux-system get kustomization payments
```

Expected: `READY=True`, `STATUS=Applied revision: main/...`.

If not ready, check: `kubectl -n flux-system describe kustomization payments`.

- [ ] **Step 2: Verify payments app is healthy**

```bash
kubectl -n payments get pods,helmrelease,secret
```

Expected: pods `Running`, `HelmRelease/payments-platform` Ready, secrets present.

- [ ] **Step 3: Remove old talos app manifests**

```bash
cd /home/blau/talos
rm k8s/apps/payments/release.yaml \
   k8s/apps/payments/secret.yaml \
   k8s/apps/payments/db.yaml \
   k8s/apps/payments/cert.yaml \
   k8s/apps/payments/httproute.yaml \
   k8s/apps/payments/kustomization.yaml
```

- [ ] **Step 4: Verify source.yaml is still present**

```bash
ls /home/blau/talos/k8s/apps/payments/
```

Expected: only `source.yaml` remains.

- [ ] **Step 5: Commit removal to talos repo**

```bash
cd /home/blau/talos
git add -A k8s/apps/payments/
git commit -m "chore(flux): remove payments manifests superseded by paymentprocessor repo"
```

- [ ] **Step 6: Push and re-verify cluster health**

```bash
cd /home/blau/talos
git push

# Wait ~2 minutes
kubectl -n payments get pods,helmrelease
kubectl -n flux-system get kustomization payments
```

Expected: all resources still healthy. The `prune: true` on the Kustomization will garbage-collect the old objects only if they are no longer in the new source — since the new `k8s/` recreates equivalent resources, nothing should be pruned unexpectedly.

---

## Task 13: Verify security properties end-to-end

Confirm all Day 9 security properties are in place on the live cluster.

- [ ] **Step 1: Verify NetworkPolicy is active**

```bash
kubectl -n payments get networkpolicy
```

Expected: `payments-platform` NetworkPolicy present.

- [ ] **Step 2: Verify RBAC is applied**

```bash
kubectl -n payments get role,rolebinding
```

Expected: `Role/payments-platform` and `RoleBinding/payments-platform` present.

- [ ] **Step 3: Verify pods run as non-root**

```bash
kubectl -n payments get pods -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.securityContext}{"\n"}{end}'
```

Expected: `runAsNonRoot:true` in pod security context.

- [ ] **Step 4: Verify ServiceAccount is dedicated (not default)**

```bash
kubectl -n payments get pods -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.serviceAccountName}{"\n"}{end}'
```

Expected: `payments-platform` (not `default`) for app pods.

- [ ] **Step 5: Verify image tag is pinned**

```bash
kubectl -n payments get pods -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.containers[0].image}{"\n"}{end}'
```

Expected: image tag is `sha-<shortsha>`, not `latest`.

- [ ] **Step 6: Verify secrets are SOPS-managed (not plaintext)**

```bash
git -C /home/blau/paymentprocessor show HEAD:k8s/secret.yaml | head -5
```

Expected: file content begins with `apiVersion: ENC[AES256_GCM` — confirms secrets are never stored in plaintext in git.

- [ ] **Step 7: Push final paymentprocessor state**

```bash
cd /home/blau/paymentprocessor
git log --oneline -8
git push
```

Expected: all Day 9 commits are on remote main.

---

## Self-Review Notes

- **Spec coverage check:**
  - Secrets Manager / SOPS-encrypted secrets → Task 5, Task 9
  - IAM roles / RBAC → Task 4
  - Pod security context (non-root) → already in chart; verified in Task 13
  - Namespace isolation → Task 4 (namespace.yaml), Task 11 (Kustomization dependsOn)
  - NetworkPolicy → Tasks 1, 3
  - Source of truth migration → Tasks 5–8, 11, 12
  - Image tag pinning → Tasks 3, 6
  - Migration SA fix → Task 2
  - infra/ marker → Task 10
  - End-to-end cluster verification → Task 13

- **Sequencing constraint:** Tasks 11 and 12 must run in order and Task 12 must not begin until the cluster is confirmed healthy after Task 11. All other tasks are independent of each other.

- **Rollback:** If anything goes wrong in Task 11/12, revert `app-payments.yaml` in the talos repo to re-point at `flux-system` / `./k8s/apps/payments`. The old files are still present until Task 12 deletes them.
