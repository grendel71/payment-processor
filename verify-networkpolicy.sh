#!/usr/bin/env bash
# verify-networkpolicy.sh
#
# Catches the silent LabelSelector field-stripping bug in
# payments-platform/templates/networkpolicy.yaml.
#
# Bug: rendering selector key-value pairs directly under
#   namespaceSelector:
#     kubernetes.io/metadata.name: envoy-gateway-system
# causes Kubernetes' APIServer to silently drop the unknown keys
# (LabelSelector only allows matchLabels/matchExpressions), leaving
# the selector as `{}` — which matches EVERY namespace/pod and turns
# the NetworkPolicy into a permissive no-op.
#
# This script:
#   1. Renders the chart with talos-like values via `helm template`
#   2. Submits each NetworkPolicy through `kubectl apply --dry-run=server`
#      (this is the only layer that catches the silent stripping)
#   3. Asserts the post-normalization object still contains the
#      expected selector keys under matchLabels
#
# Exit codes:
#   0 — all selectors preserved (fix is in place)
#   1 — at least one selector was silently stripped (bug present)
set -euo pipefail

CHART_DIR="/home/blau/paymentprocessor/payments-platform"
VALUES_FILE="/tmp/np-verify-values.yaml"
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

# Use the talos cluster kubeconfig — it's a real apiserver that
# normalizes LabelSelectors the same way etcd storage would.
# Local kubectl may point at minikube (unreachable) or no cluster at all.
export KUBECONFIG="${KUBECONFIG:-/home/blau/talos/.kubeconfig.dev}"
if [[ ! -f "$KUBECONFIG" ]]; then
  echo "FAIL: KUBECONFIG=$KUBECONFIG not found"
  exit 1
fi

cat > "$VALUES_FILE" <<'YAML'
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
      cidr: ""
      podSelector:
        cnpg.io/cluster: payments-db
      namespaceSelector:
        kubernetes.io/metadata.name: payments
      port: 5432
migrations:
  enabled: true
database:
  existingSecret:
    name: paymentprocessor-db
    key: DATABASE_URL
YAML

echo "==> Rendering chart with talos-like values"
echo "    KUBECONFIG: $KUBECONFIG"
echo "    current-context: $(kubectl config current-context 2>&1)"
helm template verify-release "$CHART_DIR" -f "$VALUES_FILE" > "$TMPDIR/rendered.yaml" 2> "$TMPDIR/helm-err.log" || {
  echo "FAIL: helm template errored:"
  cat "$TMPDIR/helm-err.log"
  exit 1
}

# Split the rendered YAML into individual documents
csplit -s -z -f "$TMPDIR/doc-" "$TMPDIR/rendered.yaml" \
  '/^---$/' '{*}' 2>/dev/null || true

# Find NetworkPolicy documents and route each through dry-run=server
fail=0
np_count=0
for f in "$TMPDIR"/doc-*; do
  kind=$(awk '/^kind:/{print $2; exit}' "$f" 2>/dev/null || echo "?")
  if [[ "$kind" != "NetworkPolicy" ]]; then
    continue
  fi
  np_count=$((np_count+1))
  name=$(awk '/^metadata:/{flag=1} flag && /^  name:/{print $2; exit}' "$f")
  echo
  echo "==> NetworkPolicy: $name"
  echo "    source bytes: $(wc -c < "$f")"

  # The CRITICAL step: covers the only layer that catches this bug.
  # Apply with --dry-run=server so the apiserver normalizes the object
  # the same way it would on a real apply. The returned JSON is what
  # would actually be stored in etcd. JSON output (not YAML) so jq
  # can parse it without a YAML→JSON conversion step.
  if ! kubectl apply --dry-run=server -f "$f" -o json > "$TMPDIR/normalized-$np_count.json" 2> "$TMPDIR/apply-err-$np_count.log"; then
    echo "FAIL: apiserver rejected NetworkPolicy:"
    cat "$TMPDIR/apply-err-$np_count.log"
    fail=1
    continue
  fi

  # Assert each expected selector key survived normalization.
  # If the chart is buggy, the unknown keys are silently dropped,
  # leaving namespaceSelector/podSelector as empty {} maps.
  checks=(
    "spec:ingress[0].from[0].namespaceSelector.matchLabels.\"kubernetes.io/metadata.name\"==envoy-gateway-system"
    "spec:ingress[0].from[0].podSelector.matchLabels.\"app.kubernetes.io/name\"==envoy-gateway"
    "spec:egress[0].to[0].namespaceSelector.matchLabels.\"kubernetes.io/metadata.name\"==kube-system"
    "spec:egress[0].to[0].podSelector.matchLabels.\"k8s-app\"==kube-dns"
  )

  # The migration NetworkPolicy has only egress, not ingress.
  if ! grep -q 'policyTypes' "$TMPDIR/normalized-$np_count.json" && \
     ! jq -e '.spec.policyTypes' "$TMPDIR/normalized-$np_count.json" >/dev/null 2>&1; then
    echo "FAIL: normalized output is empty or malformed"
    fail=1
    continue
  fi

  # Detect bug directly: empty namespaceSelector/podSelector maps
  # in the normalized object mean the chart is buggy.
  # For each NetworkPolicy rule type that exists in the spec, verify
  # that selector maps survive normalization (NOT left as `{}` or null).
  # A missing rule type (e.g. ingress on the migration NP) is fine.
  empty_ns=$(jq -r '
    [
      (.spec.ingress[0].from[0].namespaceSelector // empty
        | if . == {} then "EMPTY" else "OK" end),
      (.spec.egress[0].to[0].namespaceSelector // empty
        | if . == {} then "EMPTY" else "OK" end)
    ] as $ns
    | $ns | if length == 0 then "NONE-APPLICABLE"
            else unique | join(",") end
  ' "$TMPDIR/normalized-$np_count.json" 2>/dev/null || echo "PARSE_ERROR")

  empty_ps=$(jq -r '
    [
      (.spec.ingress[0].from[0].podSelector // empty
        | if . == {} then "EMPTY" else "OK" end),
      (.spec.egress[0].to[0].podSelector // empty
        | if . == {} then "EMPTY" else "OK" end)
    ] as $ps
    | $ps | if length == 0 then "NONE-APPLICABLE"
            else unique | join(",") end
  ' "$TMPDIR/normalized-$np_count.json" 2>/dev/null || echo "PARSE_ERROR")

  echo "    normalized namespaceSelector: $empty_ns"
  echo "    normalized podSelector:       $empty_ps"

  if [[ "$empty_ns" == *"EMPTY"* ]] || [[ "$empty_ps" == *"EMPTY"* ]] \
     || [[ "$empty_ns" == "PARSE_ERROR" ]] || [[ "$empty_ps" == "PARSE_ERROR" ]]; then
    echo "FAIL: at least one selector was silently stripped to {}"
    jq -r '"spec:\n" + (.spec | tostring)' "$TMPDIR/normalized-$np_count.json" 2>/dev/null \
      | head -40 | sed 's/^/      /'
    fail=1
    continue
  fi

  # Also assert specific expected key/values survived (defense in depth)
  # — only applies to NetworkPolicies with an ingress rule.
  if jq -e '.spec.ingress[0]' "$TMPDIR/normalized-$np_count.json" >/dev/null 2>&1; then
    ingress_ns=$(jq -r '.spec.ingress[0].from[0].namespaceSelector.matchLabels."kubernetes.io/metadata.name" // "MISSING"' "$TMPDIR/normalized-$np_count.json")
    ingress_pod=$(jq -r '.spec.ingress[0].from[0].podSelector.matchLabels."app.kubernetes.io/name" // "MISSING"' "$TMPDIR/normalized-$np_count.json")
    echo "    ingress namespaceSelector.kubernetes.io/metadata.name = $ingress_ns"
    echo "    ingress podSelector.app.kubernetes.io/name           = $ingress_pod"

    if [[ "$ingress_ns" != "envoy-gateway-system" ]] || [[ "$ingress_pod" != "envoy-gateway" ]]; then
      echo "FAIL: ingress selector value did not survive normalization"
      fail=1
      continue
    fi
  fi

  # DB egress: every NetworkPolicy rendered by this chart has egress.
  # DNS selector always comes first (egress[0]); DB selector comes second
  # (egress[1]) only if the chart emits it (only when DB egress is configured).
  if jq -e '.spec.egress[1]' "$TMPDIR/normalized-$np_count.json" >/dev/null 2>&1; then
    db_ns=$(jq -r '.spec.egress[1].to[0].namespaceSelector.matchLabels."kubernetes.io/metadata.name" // "MISSING"' "$TMPDIR/normalized-$np_count.json")
    db_pod=$(jq -r '.spec.egress[1].to[0].podSelector.matchLabels."cnpg.io/cluster" // "MISSING"' "$TMPDIR/normalized-$np_count.json")
    echo "    db-egress namespaceSelector.kubernetes.io/metadata.name = $db_ns"
    echo "    db-egress podSelector.cnpg.io/cluster                   = $db_pod"

    if [[ "$db_ns" != "payments" ]] || [[ "$db_pod" != "payments-db" ]]; then
      echo "FAIL: db-egress selector value did not survive normalization"
      fail=1
      continue
    fi
  fi

  echo "    OK"
done

echo
if [[ $np_count -eq 0 ]]; then
  echo "FAIL: no NetworkPolicy documents rendered"
  exit 1
fi

if [[ $fail -ne 0 ]]; then
  echo "FAIL: NetworkPolicy selectors were silently stripped by apiserver"
  echo "      Fix: in networkpolicy.yaml, wrap selector maps under matchLabels"
  exit 1
fi

echo "PASS: all $np_count NetworkPolicy document(s) preserved selectors through apiserver normalization"
