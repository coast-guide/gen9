#!/usr/bin/env bash
# What the stacks' pinned images are behind: Renovate's dry run (its "local" platform, lookup mode,
# docs.renovatebot.com/modules/platform/local) over a copy of the compose files and Dockerfiles, so
# nothing in the repository can change. Pins are `tag@digest`, so an image rebuilt under the same
# tag (a base image's fixes) goes unnoticed without this. Used by `make updates`.
#
#   scripts/updates.sh
set -euo pipefail
cd "$(dirname "$0")/.."

command -v node >/dev/null || { echo "make updates needs Node.js (24 or newer) for Renovate" >&2; exit 1; }
[ -x scripts/updates/node_modules/.bin/renovate ] || npm ci --prefix scripts/updates --silent --no-audit --no-fund

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
# The stacks' files only: not Langfuse's upstream file (never edited; compose.override.yaml pins its
# images) nor the probes under gen9-agent/explore
# One more pin lives outside them: the chats' environment image (settings.py)
{ git ls-files | grep -E '(^|/)(compose[^/]*\.ya?ml|Dockerfile[^/]*)$' | grep -v '^gen9-agent/explore/'
  echo gen9-agent/src/gen9_agent/settings.py; } |
  while read -r file; do mkdir -p "$work/src/$(dirname "$file")"; cp "$file" "$work/src/$file"; done
# Renovate reads it through a regex manager: a quoted "image:tag@sha256:…"
# The gen9-* images are built here, from Dockerfiles whose own images it checks: no registry has them
custom='[{"customType":"regex","managerFilePatterns":["/(^|/)settings\\.py$/"],"matchStrings":["\"(?<depName>[a-z0-9./-]+):(?<currentValue>[A-Za-z0-9._-]+)@(?<currentDigest>sha256:[0-9a-f]{64})\""],"datasourceTemplate":"docker"}]'

echo "Looking up every pinned image's tag and releases (about a minute)…"
(cd "$work/src" && LOG_LEVEL=debug LOG_FORMAT=json RENOVATE_ONBOARDING=false RENOVATE_REQUIRE_CONFIG=optional \
  RENOVATE_ENABLED_MANAGERS='["docker-compose","dockerfile","custom.regex"]' RENOVATE_CUSTOM_MANAGERS="$custom" \
  RENOVATE_PACKAGE_RULES='[{"matchPackageNames":["/^gen9-/"],"enabled":false}]' \
  "$OLDPWD/scripts/updates/node_modules/.bin/renovate" --platform=local >"$work/log.json" 2>&1) ||
  { echo "Renovate failed; its log's last lines:" >&2; tail -5 "$work/log.json" >&2; exit 1; }
node scripts/updates/summary.mjs "$work/log.json"
