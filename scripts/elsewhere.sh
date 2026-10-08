#!/usr/bin/env bash
# Which of the given stacks belong to another copy of Gen9 on this Docker host. Gen9's stacks have
# fixed names (gen9-STACK: their networks and containers reach each other by them), so one Gen9
# runs on a Docker host, and a second copy's make commands would act on the first one's containers
# and volumes: its `make up` recreating them with its own secrets, its `make wipe` deleting their
# data. Compose labels each container with the folder it was started from
# (com.docker.compose.project.working_dir). Prints "gen9-STACK FOLDER" for each stack whose
# containers, running or stopped, came from another folder, and exits 1 if there is one
# (docs/plans/manual-e2e.md, P8-R2).
#
#   scripts/elsewhere.sh STACK...
#   scripts/elsewhere.sh --refuse ACTION STACK...    says why `make ACTION` won't run, on stderr
set -euo pipefail
cd "$(dirname "$0")/.."
refuse=
if [ "${1:-}" = --refuse ]; then refuse=$2; shift 2; fi
here=$(pwd -P)
status=0
found=()
for stack in "$@"; do
  while IFS= read -r dir; do
    [ -n "$dir" ] || continue
    # The folder as this one is compared: links resolved, when it still exists
    real=$(cd "$dir" 2>/dev/null && pwd -P || printf '%s' "$dir")
    if [ "$real" != "$here/gen9-$stack" ]; then
      found+=("gen9-$stack ${real%/gen9-"$stack"}")
      status=1
      break
    fi
  done < <(docker ps -a --filter "label=com.docker.compose.project=gen9-$stack" \
    --filter label=com.docker.compose.oneoff --format '{{.Label "com.docker.compose.project.working_dir"}}' | sort -u)
done
if [ -z "$refuse" ]; then
  [ ${#found[@]} -eq 0 ] || printf '%s\n' "${found[@]}"
elif [ "$status" -ne 0 ]; then
  {
    echo "Nothing done: make $refuse here would act on another copy of Gen9."
    for f in "${found[@]}"; do echo "  ${f%% *} runs from ${f#* }"; done
    echo "One Gen9 runs on a Docker host (its stacks have fixed names). Run make $refuse in that"
    echo "folder, or stop that copy there first (make down)."
  } >&2
fi
exit "$status"
