#!/usr/bin/env bash
# The images Gen9 builds and runs: an SBOM of each, and a scan of them for known vulnerabilities
# (docs/operations.md, "Images: SBOMs and known vulnerabilities"; docs/plans/manual-e2e.md, P6-D1).
# `make audit` reads the apps' locked dependencies; this reads all an image holds: its OS packages,
# the Python or Node its base installs, and what the app brings.
#
# Syft and Grype run in gen9-sbom (scripts/sbom/Dockerfile: their release tarballs, pinned by
# verified checksums). An image reaches Syft as `docker save`'s archive, so neither tool is given
# Docker's socket, and neither has a network while it reads one: only Grype's database update has.
#
#   scripts/sbom.sh sbom   a CycloneDX SBOM per image, in scripts/sbom/out/ (STACKS: which stacks)
#   scripts/sbom.sh scan   Grype over them, after updating its database: per image, what has a
#                          fix; fails on a High or Critical one scripts/sbom/grype.yaml doesn't accept
set -euo pipefail
cd "$(dirname "$0")/.."

TOOLS=gen9-sbom:syft-1.52.0-grype-0.119.0
OUT=scripts/sbom/out
DB=gen9-sbom-grype-db
STACKS=${STACKS:-postgres keycloak langfuse temporal models sandbox agent ui edge}

tools() { docker build -q -t "$TOOLS" scripts/sbom >/dev/null; }
asked() { case " $STACKS " in *" $1 "*) return 0 ;; *) return 1 ;; esac; }

# Every image of the stacks, all profiles included, and two the sandbox server starts: the execd it
# adds to each environment (config.toml's), and the environments' own image (SANDBOX_IMAGE in
# gen9-agent/.env, or its default in settings.py)
images() {
  for s in $STACKS; do
    (cd "gen9-$s" && docker compose --profile '*' config --images) ||
      echo "gen9-$s: its settings are missing (make setup); its images are left out" >&2
  done
  if asked sandbox; then sed -n 's/^execd_image = "\(.*\)"$/\1/p' gen9-sandbox/config.toml; fi
  if asked agent; then
    { sed -n 's/^SANDBOX_IMAGE=//p' gen9-agent/.env 2>/dev/null
      sed -n 's/^ *sandbox_image: str = "\(.*\)"$/\1/p' gen9-agent/src/gen9_agent/settings.py; } | head -1
  fi
}

sbom() {
  tools
  mkdir -p "$OUT" && rm -f "$OUT"/*.cdx.json
  # Under the repository, which Docker Desktop shares with its VM (a system temporary folder may not be)
  work=$(mktemp -d "$PWD/$OUT/.work.XXXXXX")
  trap 'rm -rf "$work"' EXIT
  images | sort -u | while read -r image; do
    name=$(printf '%s' "$image" | sed 's/@sha256:.*//; s#[/:]#_#g')
    if ! docker image inspect "$image" >/dev/null 2>&1; then
      echo "  $image: not on this machine (a profile not in use), left out"; continue
    fi
    docker save "$image" -o "$work/image.tar"
    # Named after the image, not the archive it was read from
    docker run --rm --network none --user "$(id -u):$(id -g)" -v "$work:/work:ro" "$TOOLS" \
      syft -q docker-archive:/work/image.tar --source-name "${image%@*}" \
      --source-version "$(docker image inspect -f '{{.Id}}' "$image")" -o cyclonedx-json >"$OUT/$name.cdx.json"
    rm -f "$work/image.tar"
    # Its packages: the components with a package URL (the others are their files)
    echo "  $image: $(tr ',' '\n' <"$OUT/$name.cdx.json" | grep -c '"purl"') packages"
  done
  echo "SBOMs (CycloneDX JSON): $OUT/"
}

scan() {
  ls "$OUT"/*.cdx.json >/dev/null 2>&1 || { echo "No SBOMs yet: make sbom" >&2; exit 1; }
  tools
  echo "Updating Grype's vulnerability database (about 3 GB the first time)…"
  docker run --rm -v "$DB:/cache" "$TOOLS" grype db update -q
  failed=""
  for file in "$OUT"/*.cdx.json; do
    name=$(basename "$file" .cdx.json)
    echo "== $name"
    docker run --rm --network none -v "$DB:/cache:ro" -e GRYPE_DB_AUTO_UPDATE=false \
      -v "$PWD/$OUT:/sbom:ro" -v "$PWD/scripts/sbom/grype.yaml:/grype.yaml:ro" "$TOOLS" \
      grype -q -c /grype.yaml "sbom:/sbom/$name.cdx.json" || failed="$failed $name"
  done
  if [ -n "$failed" ]; then
    echo; echo "A High or Critical vulnerability with a fix, not accepted in scripts/sbom/grype.yaml:$failed" >&2; exit 1
  fi
}

case "${1:-}" in
  sbom) sbom ;;
  scan) scan ;;
  *) echo "usage: scripts/sbom.sh sbom|scan" >&2; exit 2 ;;
esac
