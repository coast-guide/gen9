#!/usr/bin/env bash
# Back up Gen9: every stack's data volumes and the settings files that hold its keys, into DIR.
# Used by `make backup DIR=...` (all stacks, or STACKS), and on a schedule by
# `make backup INTO=... KEEP=...`.
#
#   scripts/backup.sh DIR STACK...      STACK: postgres keycloak langfuse temporal models sandbox agent ui edge
#   scripts/backup.sh --into ROOT --keep N STACK...
#
# With --into, the backup is ROOT/gen9-backup-<UTC time>, and once it is complete only the newest N
# complete backups in ROOT stay: a backup still holds the people deleted after it was made, and the
# ICO's guidance on erasure asks that it be "replaced in line with an established schedule"
# (docs/plans/manual-e2e.md, P5-B4). Only folders this script made are removed (their manifest
# says so); a backup that didn't finish goes once a newer one has.
# A cold backup: the stacks' containers stop while their volumes are copied (Langfuse's guide for
# Docker installs stops ClickHouse the same way), each volume as a tar archive (Docker's "Back up,
# restore, or migrate data volumes"), then they start again. The settings files go with them:
# without their keys the data can't be read (docs/secrets.md), so DIR is readable by you alone and
# is as secret as the .env files. Left out: the local profile's downloaded models (downloaded
# again) and the chats' running environments (temporary by design). Restore with
# `make restore DIR=...`, into the same version of Gen9.
set -euo pipefail
cd "$(dirname "$0")/.."

usage() { echo "usage: scripts/backup.sh DIR STACK... | scripts/backup.sh --into ROOT --keep N STACK..." >&2; exit 2; }
ROOT='' KEEP=''
if [ "${1:-}" = --into ]; then
  [ $# -ge 5 ] && [ "$3" = --keep ] || usage
  ROOT=$2 KEEP=$4
  [[ $KEEP =~ ^[1-9][0-9]*$ ]] || { echo "--keep takes how many backups to keep, 1 or more." >&2; exit 2; }
  mkdir -p "$ROOT"
  DIR="$ROOT/gen9-backup-$(date -u +%Y-%m-%dT%H%M%SZ)"
  shift 4
else
  [ $# -ge 2 ] || usage
  DIR=$1
  shift
fi
STACKS=("$@")
# Never another copy of Gen9's stacks: a backup stops them (scripts/elsewhere.sh)
scripts/elsewhere.sh --refuse backup "${STACKS[@]}" || exit 1
if [ -e "$DIR" ] && [ -n "$(ls -A "$DIR" 2>/dev/null)" ]; then
  echo "$DIR isn't empty: name a new folder for the backup." >&2
  exit 1
fi
umask 077
if [ -e "$DIR" ]; then existed=true; else existed=false; fi
mkdir -p "$DIR/volumes" "$DIR/settings"
DIR=$(cd "$DIR" && pwd)

# Docker must be able to mount the folder, and Docker Desktop shares only some of the host's paths
# with containers ("Mounts denied" otherwise; Settings > Resources > File sharing). Found out now,
# before anything stops: a backup into a folder it couldn't mount stopped every stack for nothing
if ! denied=$(docker run --rm -v "$DIR/volumes:/backup" alpine:3 true 2>&1); then
  rmdir "$DIR/volumes" "$DIR/settings" 2>/dev/null || true
  $existed || rmdir "$DIR" 2>/dev/null || true
  echo "Docker can't mount $DIR, so nothing was stopped and nothing is backed up:" >&2
  echo "$denied" | grep -v '^$' | head -3 >&2
  echo "With Docker Desktop, name a folder under a path it shares with containers: your home folder is one." >&2
  exit 1
fi

# Each stack's settings files (as scripts/wipe.sh lists them)
generated() {
  case $1 in
    postgres) echo gen9-postgres/.env gen9-agent/postgres.local.env gen9-agent/postgres-app.local.env ;;
    keycloak) echo gen9-keycloak/.env gen9-ui/keycloak.local.env gen9-agent/keycloak.local.env gen9-temporal/keycloak.local.env ;;
    ui) echo gen9-ui/.env ;;
    agent) echo gen9-agent/.env ;;
    langfuse) echo gen9-langfuse/.env gen9-agent/langfuse.local.env ;;
    sandbox) echo gen9-sandbox/.env gen9-agent/sandbox.local.env ;;
    temporal) echo gen9-temporal/.env gen9-temporal/tls.local.env ;;
    models) echo gen9-models/.env gen9-agent/models.local.env gen9-agent/models-api.local.env gen9-agent/models-evals.local.env ;;
    edge) echo gen9-edge/.env gen9-keycloak/edge.local.env ;;
    *) echo "unknown stack: $1" >&2; exit 2 ;;
  esac
}

# A size as du gives it (BSD du pads short sizes with a space)
size() { du -sh "$1" | awk '{print $1}'; }

{
  echo "# Gen9 backup: $(date -u +%Y-%m-%dT%H:%M:%SZ), commit $(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
  echo "stacks ${STACKS[*]}"
} > "$DIR/manifest"

# Stop every container of these stacks, noting the running ones to start again
running=()
for stack in "${STACKS[@]}"; do
  for id in $(docker ps -q --filter "label=com.docker.compose.project=gen9-$stack"); do running+=("$id"); done
done
# Started again the way make up starts them: in order, each waiting for what it depends on
restart() {
  if [ ${#running[@]} -gt 0 ]; then
    echo "Starting the stacks again…"
    make --no-print-directory up STACKS="${STACKS[*]}" >/dev/null 2>&1 ||
      echo "They didn't all start again: run make up to see why." >&2
  fi
}
trap restart EXIT
if [ ${#running[@]} -gt 0 ]; then
  echo "Stopping ${#running[@]} containers, so the data is copied as it stands…"
  docker stop "${running[@]}" >/dev/null
fi

for stack in "${STACKS[@]}"; do
  for volume in $(docker volume ls -q --filter "label=com.docker.compose.project=gen9-$stack"); do
    case $volume in
      gen9-models_ollama_models | gen9-models_reranker_models) continue ;; # downloaded again
    esac
    labels=$(docker volume inspect -f '{{range $k, $v := .Labels}}{{$k}}={{$v}} {{end}}' "$volume")
    docker run --rm -v "$volume:/volume:ro" -v "$DIR/volumes:/backup" alpine:3 \
      tar czf "/backup/$volume.tar.gz" -C /volume .
    echo "volume $volume $labels" >> "$DIR/manifest"
    echo "  $volume: $(size "$DIR/volumes/$volume.tar.gz")"
  done
  for file in $(generated "$stack"); do
    [ -f "$file" ] || continue
    mkdir -p "$DIR/settings/$(dirname "$file")"
    cp -p "$file" "$DIR/settings/$file"
    echo "settings $file" >> "$DIR/manifest"
  done
done
chmod -R go-rwx "$DIR"
count() { local n; n=$(grep -c "^$1 " "$DIR/manifest"); if [ "$n" = 1 ]; then echo "1 $2"; else echo "$n $3"; fi; }
echo "Backed up $(count volume volume volumes) and $(count settings "settings file" "settings files") to $DIR ($(size "$DIR"))."
echo "It holds the keys to the data: keep it where only you can read it."
echo "complete $(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$DIR/manifest"

# The schedule's rotation: the newest KEEP complete backups stay, older ones go, as do backups that
# didn't finish, once a newer one has. A folder counts only if its manifest is a Gen9 backup's.
if [ -n "$ROOT" ]; then
  complete=() unfinished=()
  for backup in "$ROOT"/gen9-backup-*; do
    if ! { [ -f "$backup/manifest" ] && head -1 "$backup/manifest" | grep -q '^# Gen9 backup: '; }; then continue; fi
    if grep -q '^complete ' "$backup/manifest"; then complete+=("$backup"); else unfinished+=("$backup"); fi
  done
  newest=${complete[${#complete[@]}-1]}
  old=$((${#complete[@]} - KEEP))
  for ((i = 0; i < old; i++)); do
    rm -rf "${complete[$i]}"
    echo "Removed the older backup $(basename "${complete[$i]}") (keeping $KEEP)."
  done
  for backup in ${unfinished[@]+"${unfinished[@]}"}; do
    [[ $backup < $newest ]] || continue
    rm -rf "$backup"
    echo "Removed $(basename "$backup"), a backup that didn't finish."
  done
fi
