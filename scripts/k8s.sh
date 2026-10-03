#!/bin/sh
# Gen9 on Kubernetes (docs/plans/deploy.md, U3; docs/operations.md, "Kubernetes"): each stack a Helm
# release of its own chart (gen9-<stack>/chart), in its own namespace gen9-<stack>, in make's order.
#
#   scripts/k8s.sh up   STACK...   namespaces, Secrets from the stacks' settings files, then
#                                  helm upgrade --install --wait, with the lock's images (images.env)
#   scripts/k8s.sh diff STACK...   what differs from what's declared (helm-diff's three-way merge,
#                                  which sees changes made by hand; Secrets by hash): exit 2 if any
#   scripts/k8s.sh reset STACK...  puts back what was changed by hand: every object replaced whole
#                                  with what the chart renders (helm --force-replace), which also
#                                  drops what a merge would keep (a variable added by kubectl set env)
#   scripts/k8s.sh down STACK...   helm uninstall, in reverse order; volumes and Secrets stay
#   scripts/k8s.sh e2e  STACK...   make e2e against the cluster: each port a stack publishes on Docker
#                                  forwarded from 127.0.0.1 to its service (re-dialled when a pod
#                                  restarts), and e2e/k8s first on PATH, its docker reaching the pods
#   scripts/k8s.sh stop-agents     as make stop-agents on Docker: every run cancelled and scheduled
#                                  task paused (gen9-agent-stop), then the worker scaled to 0
#   scripts/k8s.sh resume-agents   the worker back, then what the stop paused unpaused
#
# The cluster is kubectl's current context, or K8S_CONTEXT. Settings: deploy/values.yaml, or the
# file in K8S_VALUES. Secrets: one per settings file make setup wrote (gen9-<stack>/.env becomes the
# Secret env, keycloak.local.env keycloak-local-env), applied server-side, so no copy of a value
# lands in an annotation; nothing here prints a value.
set -eu
cd "$(dirname "$0")/.."

action=${1:-}; shift || true
case "$action" in
  stop-agents|resume-agents) ;;
  *) [ -n "$action" ] && [ $# -gt 0 ] || { echo "usage: scripts/k8s.sh up|diff|reset|down|e2e STACK... | stop-agents | resume-agents" >&2; exit 2; } ;;
esac
ctx=${K8S_CONTEXT:-$(kubectl config current-context)}
values=${K8S_VALUES:-deploy/values.yaml}
kc() { kubectl --context "$ctx" "$@"; }

# Gen9's images by digest, from the lock make up IMAGES=… wrote: --set global.images.<name>=<ref>
images() {
  [ -f images.env ] || { echo "no images.env: give a lock (make k8s-up IMAGES=<lock>); a cluster runs only images by digest" >&2; exit 1; }
  sed -n 's/^GEN9_\([A-Z_]*\)_IMAGE=\(.*\)$/\1 \2/p' images.env | while read -r name ref; do
    # SANDBOX_EGRESS -> sandboxEgress
    key=$(echo "$name" | tr '[:upper:]' '[:lower:]' | awk -F_ '{ for (i = 2; i <= NF; i++) $i = toupper(substr($i, 1, 1)) substr($i, 2); print }' OFS='')
    printf ' --set global.images.%s=%s' "$key" "$ref"
  done
}

# The names of the keys the stack's .env gives a value (never the values), for the chart to read a
# Compose ${X:-default}, ${X:?…} or ${X:+…} that .env sets from the Secret env, as Compose reads it
from_env() {
  [ -f "gen9-$1/.env" ] || { echo "[]"; return; }
  printf '[%s]' "$(sed -n 's/^\([A-Z_][A-Z0-9_]*\)=..*/"\1"/p' "gen9-$1/.env" | paste -sd, -)"
}

# The shared settings (the values file's settings.X) each read by some stack's Compose files: one
# that none reads would install and do nothing. Helm reads the values file (a throwaway chart that
# prints the keys); a stack's own settings its chart checks
check_settings() {
  chart=$(mktemp -d)
  mkdir "$chart/templates"
  printf 'apiVersion: v2\nname: gen9-settings\nversion: 0.0.0\n' > "$chart/Chart.yaml"
  cat > "$chart/templates/keys.yaml" <<'TEMPLATE'
{{ range keys (.Values.settings | default dict) }}# {{ . }}
{{ end }}
TEMPLATE
  keys=$(helm template s "$chart" -f "$values" | sed -n 's/^# \([A-Za-z_][A-Za-z0-9_]*\)$/\1/p')
  rm -rf "$chart"
  unread=""
  for key in $keys; do
    grep -qE "\\\$\\{?$key([^A-Za-z0-9_]|\$)" gen9-*/compose.yaml gen9-*/compose.override.yaml gen9-langfuse/docker-compose.yml ||
      unread="$unread $key"
  done
  [ -z "$unread" ] || { for key in $unread; do echo "settings.$key: no stack's Compose files read $key" >&2; done; exit 2; }
}

# The stack's settings files as Secrets: name, then file
secrets() {
  for f in "gen9-$1/.env" "gen9-$1"/*.local.env; do
    [ -f "$f" ] || continue
    name=$(basename "$f" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]\{1,\}/-/g; s/^-*//; s/-*$//')
    echo "$name $f"
  done
}

# Each settings file's hash, by the name of the Secret made of it, as JSON: the chart puts them in
# the pods' annotation, so a file that changed rolls the pods that read it (secretHashes)
secret_hashes() {
  secrets "$1" | { sep=""; printf '{'; while read -r name file; do printf '%s"%s":"%s"' "$sep" "$name" "$(sha256 "$file")"; sep=","; done; printf '}'; }
}

# What the cluster needs before a stack's chart (gen9-<stack>/chart/prerequisites.txt: <URL> <sha256>
# per line; agent-sandbox for gen9-sandbox), each checked against its digest, then applied
# server-side (apply) or compared with what runs (diff, which writes $2 when it differs)
sha256() { if command -v sha256sum >/dev/null; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1; }
prerequisites() {
  list="gen9-$1/chart/prerequisites.txt"
  [ -f "$list" ] || return 0
  grep -vE '^[[:space:]]*(#|$)' "$list" | while read -r url sum; do
    file=$(mktemp)
    curl -fsSL "$url" -o "$file"
    [ "$(sha256 "$file")" = "$sum" ] || { echo "$url: not the file pinned in $list" >&2; rm -f "$file"; exit 1; }
    if [ "${2:-}" ]; then
      kc diff --server-side --force-conflicts -f "$file" || echo drift > "$2"
    else
      kc apply --server-side --force-conflicts -f "$file" >/dev/null && echo "prerequisite: $url"
    fi
    rm -f "$file"
  done
}

# Each settings file as its Secret, exactly: replaced whole when it exists, so a key added by hand
# goes too (an apply would keep it), created when it doesn't
put_secrets() {
  secrets "$1" | while read -r name file; do
    manifest=$(kc -n "gen9-$1" create secret generic "$name" --from-env-file="$file" --dry-run=client -o yaml)
    if kc -n "gen9-$1" get secret "$name" >/dev/null 2>&1; then
      printf '%s\n' "$manifest" | kc replace -f - >/dev/null
    else
      printf '%s\n' "$manifest" | kc create -f - >/dev/null
    fi
    echo "secret $name: from $file"
  done
}

case "$action" in
  up|reset)
    check_settings
    set_images=$(images)
    for s in "$@"; do
      ns="gen9-$s"
      [ -d "gen9-$s/chart" ] || { echo "== $ns: no chart yet, left out"; continue; }
      echo "== $ns"
      prerequisites "$s"
      kc create namespace "$ns" --dry-run=client -o yaml | kc apply --server-side -f - >/dev/null
      put_secrets "$s"
      helm dependency update "gen9-$s/chart" >/dev/null
      if [ "$action" = reset ]; then how="--server-side=false --force-replace"; else how="--server-side=true --force-conflicts"; fi
      # shellcheck disable=SC2086 # set_images and how are lists of flags
      helm --kube-context "$ctx" upgrade --install "$ns" "gen9-$s/chart" -n "$ns" -f "$values" \
        $set_images --set-json "fromEnv=$(from_env "$s")" --set-json "secretHashes=$(secret_hashes "$s")" $how --wait --timeout 15m
    done ;;
  diff)
    check_settings
    set_images=$(images)
    drift=0
    for s in "$@"; do
      ns="gen9-$s"
      [ -d "gen9-$s/chart" ] || continue
      echo "== $ns"
      prerequisites "$s" "${TMPDIR:-/tmp}/gen9-k8s-drift.$$"
      helm dependency update "gen9-$s/chart" >/dev/null
      # shellcheck disable=SC2086 # set_images is a list of flags
      helm --kube-context "$ctx" diff upgrade "$ns" "gen9-$s/chart" -n "$ns" -f "$values" $set_images --set-json "fromEnv=$(from_env "$s")" \
        --set-json "secretHashes=$(secret_hashes "$s")" \
        --three-way-merge --detailed-exitcode --no-color --no-hooks || { rc=$?; [ "$rc" = 2 ] && drift=1 || exit "$rc"; }
      # (Hooks aside: the one-shot Jobs are gone once done, as they should be)
      # What a merge keeps and so no diff shows: a field someone else added (kubectl set env, edit,
      # patch). Each field has an owner (managedFields): anything of the stack's owned by other than
      # Helm and the cluster's own controllers was changed by hand. Scaling is left to the diff
      # above, which compares the counts; a rollout restart changes nothing that runs
      # shellcheck disable=SC2016 # Python, in single quotes on purpose
      kc -n "$ns" get deploy,statefulset,job,svc,configmap,networkpolicy -l app.kubernetes.io/part-of=gen9 \
        -o json --show-managed-fields | python3 -c '
import json, sys
found = False
for obj in json.load(sys.stdin)["items"]:
    for f in obj["metadata"].get("managedFields", []):
        if f["manager"] in ("helm", "kube-controller-manager", "kubectl-rollout") or f.get("subresource"):
            continue
        found = True
        print("%s/%s: changed by %s (%s)" % (obj["kind"], obj["metadata"]["name"], f["manager"], f["operation"]))
sys.exit(2 if found else 0)
' || drift=1
      secrets "$s" | while read -r name file; do
        want=$(kc -n "$ns" create secret generic "$name" --from-env-file="$file" --dry-run=client -o jsonpath='{.data}' | sha256sum)
        have=$(kc -n "$ns" get secret "$name" -o jsonpath='{.data}' 2>/dev/null | sha256sum)
        [ "$want" = "$have" ] || { echo "secret $name differs from $file"; echo drift > "${TMPDIR:-/tmp}/gen9-k8s-drift.$$"; }
      done
      [ ! -f "${TMPDIR:-/tmp}/gen9-k8s-drift.$$" ] || { drift=1; rm -f "${TMPDIR:-/tmp}/gen9-k8s-drift.$$"; }
    done
    exit $((drift * 2)) ;;
  down)
    for s in $(printf '%s\n' "$@" | sed '1!G;h;$!d'); do
      ns="gen9-$s"
      helm --kube-context "$ctx" status "$ns" -n "$ns" >/dev/null 2>&1 || continue
      echo "== $ns"
      helm --kube-context "$ctx" uninstall "$ns" -n "$ns" --wait
    done ;;
  e2e)
    # The ports each stack publishes on Docker (127.0.0.1:<published> -> <target>), from Compose
    forwards=""
    for s in "$@"; do
      forwards="$forwards $(cd "gen9-$s" && docker compose config --format json | python3 -c '
import json, sys
for name, svc in json.load(sys.stdin)["services"].items():
    if not svc.get("profiles"):
        for p in svc.get("ports", []):
            print("%s/%s/%s/%s" % (sys.argv[1], name, p["published"], p["target"]))
' "$s")"
    done
    pids=""
    for f in $forwards; do
      stack=${f%%/*}; rest=${f#*/}; svc=${rest%%/*}; rest=${rest#*/}; pub=${rest%%/*}; tgt=${rest#*/}
      ( while :; do kc -n "gen9-$stack" port-forward --address 127.0.0.1 "svc/$svc" "$pub:$tgt" >/dev/null 2>&1; sleep 1; done ) &
      pids="$pids $!"
    done
    stop_forwards() { for p in $pids; do pkill -P "$p" 2>/dev/null; kill "$p" 2>/dev/null; done; }
    trap stop_forwards EXIT INT TERM
    for _ in $(seq 60); do curl -fsS -o /dev/null http://127.0.0.1:14000/api/health 2>/dev/null && break; sleep 2; done
    # E2E_SHAPE: a check that runs a make target of Docker's runs its k8s- one (stop.mjs)
    PATH="$PWD/e2e/k8s:$PATH" E2E_SHAPE=kubernetes make e2e ;;
  stop-agents)
    kc -n gen9-agent exec deploy/worker -c worker -- gen9-agent-stop
    kc -n gen9-agent scale deploy/worker --replicas=0 >/dev/null
    kc -n gen9-agent wait --for=delete pod -l app.kubernetes.io/name=worker --timeout=120s >/dev/null 2>&1 || true ;;
  resume-agents)
    kc -n gen9-agent scale deploy/worker --replicas=1 >/dev/null
    kc -n gen9-agent rollout status deploy/worker --timeout=300s >/dev/null
    kc -n gen9-agent exec deploy/worker -c worker -- gen9-agent-stop --resume ;;
  *) echo "usage: scripts/k8s.sh up|diff|reset|down|e2e STACK... | stop-agents | resume-agents" >&2; exit 2 ;;
esac
