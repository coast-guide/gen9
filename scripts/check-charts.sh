#!/bin/sh
# Every stack's Helm chart (gen9-<stack>/chart), rendered with a stand-in lock and checked: helm
# lint, then every object against Kubernetes' own schemas with kubeconform -strict (docs/plans/
# deploy.md, U3). The library's template already fails on what would drift from compose.yaml: a
# Compose service with no entry in the chart's services, a file it mounts that isn't linked in, an
# image not by digest. Run by CI; needs helm and kubeconform.
#
#   scripts/check-charts.sh            KUBERNETES_VERSION=1.37.1 by default
set -eu
cd "$(dirname "$0")/.."

digest=$(printf '0%.0s' $(seq 64))
images=""
for key in agent ui keycloak postgres sandbox sandboxEgress sandboxExecd; do
  images="$images --set global.images.$key=registry.invalid/gen9/$key@sha256:$digest"
done

status=0
for chart in gen9-*/chart; do
  echo "== $chart"
  helm dependency update "$chart" >/dev/null
  # shellcheck disable=SC2086 # images is a list of flags
  # (lint can't be told the cluster's APIs: gen9-sandbox's admission policy is rendered below)
  helm lint "$chart" -f deploy/values.yaml $images --set sandboxes.hardening=optional --quiet 2>/dev/null || status=1
  # shellcheck disable=SC2086
  rendered=$(helm template check "$chart" -f deploy/values.yaml $images \
    --api-versions admissionregistration.k8s.io/v1/MutatingAdmissionPolicy 2>/dev/null) || { echo "$chart: doesn't render"; status=1; continue; }
  printf '%s\n' "$rendered" | kubeconform -strict -summary -kubernetes-version "${KUBERNETES_VERSION:-1.37.1}" - || status=1
  # A Compose variable the translation left as written (gen9-lib's gen9.interpolate), in what
  # Kubernetes reads: workloads' env, commands and args, not files carried in ConfigMaps
  # shellcheck disable=SC2016 # Python, in single quotes on purpose
  left=$(printf '%s\n' "$rendered" | python3 -c '
import re, sys
for doc in sys.stdin.read().split("\n---\n"):
    kind = re.search(r"^kind: (\S+)", doc, re.M)
    if kind and kind[1] != "ConfigMap" and "${" in doc:
        print(kind[1], re.search(r"^  name: (\S+)", doc, re.M)[1])
')
  [ -z "$left" ] || { echo "$chart: Compose variables left unresolved in: $left"; status=1; }
done
exit $status
