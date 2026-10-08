#!/usr/bin/env bash
# Restore a backup made by scripts/backup.sh (make backup): the backed-up stacks' settings files
# and data volumes replace what those stacks hold now, then they start. Used by
# `make restore DIR=...`.
#
#   scripts/restore.sh [--yes] DIR
#
# What the stacks hold now goes first (scripts/wipe.sh), so it asks you to type "yes"; --yes skips
# the question, and without a terminal it refuses instead of waiting. The settings files come back
# with the data because the data is encrypted with their keys (docs/secrets.md). Restore into the
# version of Gen9 that made the backup: a volume is the store's own files, which another major
# version of Postgres or ClickHouse doesn't read.
set -euo pipefail
cd "$(dirname "$0")/.."

YES=false DIR=
for arg in "$@"; do
  case $arg in
    --yes) YES=true ;;
    -*) echo "unknown option: $arg" >&2; exit 2 ;;
    *) DIR=$arg ;;
  esac
done
[ -n "$DIR" ] || { echo "usage: scripts/restore.sh [--yes] DIR" >&2; exit 2; }
[ -f "$DIR/manifest" ] || { echo "$DIR isn't a Gen9 backup (no manifest): make one with make backup DIR=..." >&2; exit 1; }
DIR=$(cd "$DIR" && pwd)
read -r -a STACKS <<< "$(sed -n 's/^stacks //p' "$DIR/manifest")"
# Never into another copy of Gen9's stacks (scripts/elsewhere.sh)
scripts/elsewhere.sh --refuse restore "${STACKS[@]}" || exit 1
made=$(head -1 "$DIR/manifest" | sed 's/^# Gen9 backup: //')
commit=$(sed -n 's/.*commit //p' "$DIR/manifest" | head -1)
here=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)

echo "Backup of ${STACKS[*]}, made $made."
# Docker must be able to mount the backup, and Docker Desktop shares only some of the host's paths
# with containers ("Mounts denied" otherwise). Found out now: the wipe below comes before the
# volumes are read, and a backup it can't mount would leave the stacks empty
if ! denied=$(docker run --rm -v "$DIR/volumes:/backup:ro" alpine:3 true 2>&1); then
  echo "Docker can't mount $DIR, so nothing changed:" >&2
  echo "$denied" | grep -v '^$' | head -3 >&2
  echo "With Docker Desktop, move the backup under a path it shares with containers: your home folder is one." >&2
  exit 1
fi
[ "$commit" = "$here" ] || echo "It was made at commit $commit; this is $here. Restore into the same version if you can."
echo "Restoring replaces, for good, everything these stacks hold now."
if ! $YES; then
  [ -t 0 ] || { echo "Refusing without a terminal to confirm: add YES=1 (make) or --yes." >&2; exit 1; }
  read -r -p "Type yes to restore: " answer
  [ "$answer" = yes ] || { echo "Nothing changed."; exit 1; }
fi

# The accounts and chats deleted after the backup was made, by id, from Gen9's audit record (by the
# person, an admin, the sweep of users deleted in Keycloak, or an earlier restore): the backup
# brings them back, so they're deleted again once the stacks are up. The ICO's guidance on erasure:
# backup data stays "beyond use" (docs/plans/manual-e2e.md, P4-E5, and its Decision Log). Read
# before the wipe when gen9-postgres is restored, as its record goes with it; after, when it isn't
stamp=${made%%,*}  # the manifest's "<time>, commit <sha>"
deleted() {
  docker exec gen9-postgres-postgres-1 psql -U postgres -d gen9_agent -tAc \
    "select case when action like '%thread%' then 'thread ' else 'user ' end || target from audit_events
     where at > '$stamp' and outcome = 'success' and target is not null and action in ('account.delete',
     'admin.user.delete', 'account.sweep', 'restore.account.delete', 'thread.delete', 'restore.thread.delete')"
}
# Whether the record reaches back to the backup: a database made since (after a loss) holds none of
# the deletions in between
covers() {
  [ "$(docker exec gen9-postgres-postgres-1 psql -U postgres -d gen9_agent -tAc \
    "select coalesce(min(at) <= '$stamp', false) from audit_events")" = t ]
}
evidence=$(mktemp)
trap 'rm -f "$evidence"' EXIT
unread=false
if [[ " ${STACKS[*]} " == *" postgres "* ]]; then
  { covers && deleted > "$evidence"; } 2>/dev/null || unread=true
fi

# What these stacks hold now: containers, volumes (not the downloaded models) and networks
scripts/wipe.sh --yes "${STACKS[@]}" >/dev/null

# Their settings, with the keys the data was encrypted with
while read -r _ file; do
  mkdir -p "$(dirname "$file")"
  cp -p "$DIR/settings/$file" "$file"
  chmod 600 "$file"
done < <(grep '^settings ' "$DIR/manifest")

# Each volume as Compose made it (its labels, so Compose takes it as its own), then its files
while read -r _ volume labels; do
  args=()
  for label in $labels; do args+=(--label "$label"); done
  docker volume create "${args[@]}" "$volume" >/dev/null
  docker run --rm -v "$volume:/volume" -v "$DIR/volumes:/backup:ro" alpine:3 \
    tar xzf "/backup/$volume.tar.gz" -C /volume
  echo "  $volume"
done < <(grep '^volume ' "$DIR/manifest")

echo "Starting ${STACKS[*]}…"
make --no-print-directory up STACKS="${STACKS[*]}"
echo "Restored the backup made $made."

# Deleted again: what the record says was deleted after the backup was made
if [[ " ${STACKS[*]} " != *" postgres "* ]]; then
  { covers && deleted > "$evidence"; } 2>/dev/null || unread=true
fi
users=() threads=()
count() { [ "$1" -eq 1 ] && echo "1 $2" || echo "$1 ${2}s"; }
while read -r kind id; do
  case $kind in
    user) [[ " ${users[*]:-} " == *" $id "* ]] || users+=("$id") ;;
    thread) [[ " ${threads[*]:-} " == *" $id "* ]] || threads+=("$id") ;;
  esac
done < "$evidence"
again=(gen9-agent-erase)
[ ${#users[@]} -eq 0 ] || again+=(--users "${users[@]}")
[ ${#threads[@]} -eq 0 ] || again+=(--threads "${threads[@]}")
if $unread; then
  echo "gen9-postgres's audit record couldn't be read, or starts after $stamp (a database made since), so accounts and chats deleted after the backup may be back." >&2
  echo "Delete them again, by id: (cd gen9-agent && docker compose exec worker gen9-agent-erase --users SUB... --threads ID...)" >&2
fi
if [ ${#again[@]} -gt 1 ]; then
  echo "Deleting again what was deleted after the backup was made: $(count ${#users[@]} account), $(count ${#threads[@]} chat)…"
  if ! (cd gen9-agent && docker compose exec -T worker "${again[@]}"); then
    echo "Not done: once gen9-agent runs, (cd gen9-agent && docker compose exec worker ${again[*]})" >&2
    exit 1
  fi
fi
