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

GATEWAY_SCHEMAS='https://raw.githubusercontent.com/datreeio/CRDs-catalog/041baf4c1d3740ca21461cab765e7432ecd073ef/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'

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
  # Under a domain: the same, with the public services' HTTPRoutes, checked against the Gateway
  # API's schema (datreeio/CRDs-catalog, Gateway API 1.6.1, pinned by commit)
  # shellcheck disable=SC2086
  routed=$(helm template check "$chart" -f deploy/values.yaml $images --set global.domain=gen9.example \
    --set global.gateway.name=gen9 --api-versions admissionregistration.k8s.io/v1/MutatingAdmissionPolicy 2>/dev/null) ||
    { echo "$chart: doesn't render under a domain"; status=1; continue; }
  printf '%s\n' "$routed" | kubeconform -strict -summary -kubernetes-version "${KUBERNETES_VERSION:-1.37.1}" \
    -schema-location default -schema-location "$GATEWAY_SCHEMAS" - || status=1
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
# The hosts people reach Gen9 by, the same in both shapes: gen9-edge's sites (Docker) and the
# charts' public services (Kubernetes' routes)
# shellcheck disable=SC2016 # Python, in single quotes on purpose
python3 -c '
import glob, re, sys
edge = set(re.findall(r"^(\S*?)\.?\{\$GEN9_DOMAIN\} \{", open("gen9-edge/Caddyfile").read(), re.M))
charts = set()
for f in glob.glob("gen9-*/chart/values.yaml"):
    charts |= set(re.findall(r"public: \{host: \"?([^\",}]*)\"?,", open(f).read()))
if edge != charts:
    print("hosts differ: gen9-edge only %s, the charts only %s" % (sorted(edge - charts), sorted(charts - edge)))
    sys.exit(1)
print("hosts: the same %d in gen9-edge and the charts" % len(edge))
' || status=1
exit $status
