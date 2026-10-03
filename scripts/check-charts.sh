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
# A release's chart takes its images from the images.lock packed inside (scripts/release-charts.sh)
# when no values give them: gen9-sandbox, which reads three, rendered with a stand-in lock only
packed=$(mktemp -d)
cp -RL gen9-sandbox/chart "$packed/gen9-sandbox"
sed "s|file://../../deploy/helm/gen9-lib|file://$PWD/deploy/helm/gen9-lib|" gen9-sandbox/chart/Chart.yaml > "$packed/gen9-sandbox/Chart.yaml"
for image in sandbox sandbox-egress sandbox-execd; do echo "registry.invalid/lock/gen9-$image@sha256:$digest"; done > "$packed/gen9-sandbox/images.lock"
helm dependency update "$packed/gen9-sandbox" >/dev/null 2>&1
from_lock=$(helm template check "$packed/gen9-sandbox" -f deploy/values.yaml \
  --api-versions admissionregistration.k8s.io/v1/MutatingAdmissionPolicy 2>/dev/null | grep -c 'registry.invalid/lock/gen9-' || true)
rm -rf "$packed"
if [ "$from_lock" -ge 3 ]; then echo "a release's chart: its images from its packed lock"; else
  echo "a release's chart doesn't take its images from its packed lock ($from_lock of 3)"; status=1; fi

# A bundled store left out (kind: none) is refused unless the setting its label gen9.external names
# is given, naming another host than the store itself, and with it renders without the store and
# what shares its profile (docs/operations.md, "External services")
# shellcheck disable=SC2016,SC2086 # Python, in single quotes on purpose; images, a list of flags
for line in $(python3 -c '
import glob, re
for stack in sorted({f.split("/")[0] for f in glob.glob("gen9-*/compose*.yaml")}):
    labels, profiles = {}, {}
    for f in sorted(glob.glob(stack + "/compose*.yaml")):
        service = None
        for line in open(f):
            m = re.match(r"^  ([a-z][a-z0-9-]*):\s*$", line)
            if m: service = m.group(1)
            m = re.match(r"^\s+gen9\.external: *([A-Z0-9_]+)", line)
            if m and service: labels[service] = m.group(1)
            m = re.match(r"^\s+profiles: *\[([a-z0-9, -]+)\]", line)
            if m and service: profiles[service] = [p.strip() for p in m.group(1).split(",")]
    for store, setting in labels.items():
        along = [s for s, ps in profiles.items() if s != store and store in ps]
        print("%s/%s/%s/%s" % (stack, store, setting, ",".join([store] + along)))
'); do
  stack=${line%%/*}; rest=${line#*/}; store=${rest%%/*}; rest=${rest#*/}; setting=${rest%%/*}; gone=${rest#*/}
  key=${stack#gen9-}
  none="--set $key.services.$store.kind=none"
  if helm template check "$stack/chart" -f deploy/values.yaml $images $none >/dev/null 2>&1; then
    echo "$stack: $store left out with no $setting wasn't refused"; status=1
  elif helm template check "$stack/chart" -f deploy/values.yaml $images $none --set "$key.settings.$setting=$store" >/dev/null 2>&1; then
    echo "$stack: $store left out with $setting naming $store itself wasn't refused"; status=1
  elif ! out=$(helm template check "$stack/chart" -f deploy/values.yaml $images $none \
    --set "$key.settings.$setting=elsewhere" 2>/dev/null); then
    echo "$stack: $store left out with $setting set doesn't render"; status=1
  elif printf '%s\n' "$out" | grep -Eq "^  name: ($(echo "$gone" | tr , '|'))$"; then
    echo "$stack: $store left out with $setting set still renders $gone"; status=1
  else
    echo "$stack: $store elsewhere when $setting names another host (left out: $gone), refused without it"
  fi
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
