#!/bin/sh
# Which of Gen9's own images the stacks run (docs/operations.md, "Run a release's images"): a lock
# (a file or URL, one `<registry>/<image>@sha256:<digest>` per line, as CI and releases write it)
# becomes images.env, which `make up` then reads, pulling each image by digest and building
# nothing. `local` removes images.env: `make up` builds them on this machine again.
#
#   scripts/images.sh <lock file or URL>
#   scripts/images.sh local
set -eu
cd "$(dirname "$0")/.."

IMAGES="gen9-agent gen9-ui gen9-keycloak gen9-postgres gen9-sandbox gen9-sandbox-egress gen9-sandbox-execd"

case "${1:-}" in
  "") echo "usage: scripts/images.sh <lock file or URL> | local" >&2; exit 2 ;;
  local)
    rm -f images.env
    echo "Gen9's images: built on this machine (images.env removed)"
    exit 0 ;;
  http://*|https://*) lock=$(curl -fsSL "$1") || { echo "couldn't download $1" >&2; exit 1; } ;;
  *) lock=$(cat "$1") || exit 1 ;;
esac

out=$(mktemp images.env.XXXXXX)
trap 'rm -f "$out"' EXIT
echo "# Gen9's own images, by digest, from $1 (scripts/images.sh; \`make up IMAGES=local\` removes it)" > "$out"
for image in $IMAGES; do
  # Exactly one line for this image, by digest, from any registry
  refs=$(printf '%s\n' "$lock" | grep -E "^[a-z0-9.:-]+(/[a-z0-9._-]+)*/$image@sha256:[0-9a-f]{64}$" || true)
  if [ "$(printf '%s' "$refs" | grep -c .)" != 1 ]; then
    echo "$1: needs exactly one line <registry>/$image@sha256:<digest>" >&2
    exit 1
  fi
  var=$(echo "$image" | tr 'a-z-' 'A-Z_')_IMAGE
  echo "$var=$refs" >> "$out"
done
mv "$out" images.env
trap - EXIT
echo "Gen9's images: by digest from $1 (images.env)"
sed -n 's/^\(GEN9_[A-Z_]*_IMAGE\)=/  \1=/p' images.env
