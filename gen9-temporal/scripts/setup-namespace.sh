#!/bin/sh
# Create Gen9's namespace and its search attributes, or bring an existing one in line (retention).
# Safe on every start. What each search attribute is for: docs/temporal.md, "Conventions".
set -eu
: "${TEMPORAL_ADDRESS:?}" "${GEN9_NAMESPACE:?}" "${GEN9_RETENTION:?}"
export TEMPORAL_ADDRESS

until temporal operator cluster health >/dev/null 2>&1; do echo "waiting for the server"; sleep 2; done

if temporal operator namespace describe -n "$GEN9_NAMESPACE" >/dev/null 2>&1; then
  temporal operator namespace update -n "$GEN9_NAMESPACE" --retention "$GEN9_RETENTION" >/dev/null
  echo "namespace $GEN9_NAMESPACE exists; retention $GEN9_RETENTION"
else
  temporal operator namespace create -n "$GEN9_NAMESPACE" --retention "$GEN9_RETENTION" \
    --description "Gen9: runs, approvals, schedules, deletions"
  echo "namespace $GEN9_NAMESPACE created; retention $GEN9_RETENTION"
fi

# A new namespace takes a moment to reach every server cache; the create below retries until then
existing=$(temporal operator search-attribute list -n "$GEN9_NAMESPACE" -o json 2>/dev/null || echo "")
for name in Gen9User Gen9Thread Gen9Kind Gen9RunState; do
  case $existing in *"\"$name\""*) continue ;; esac
  tries=0
  until temporal operator search-attribute create -n "$GEN9_NAMESPACE" --name "$name" --type Keyword </dev/null >/dev/null 2>&1; do
    tries=$((tries + 1)); [ $tries -lt 30 ] || { echo "could not create search attribute $name" >&2; exit 1; }
    sleep 2
  done
  echo "search attribute $name created"
done
