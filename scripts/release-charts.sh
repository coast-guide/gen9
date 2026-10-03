#!/bin/sh
# A release's charts (docs/plans/deploy.md, U7): each gen9-<stack>/chart packaged with the release's
# version and its images.lock inside, which the library reads the images from when no values give
# them, so `helm install oci://…/gen9-<stack> --version X` runs that release's images; then pushed
# as an OCI artifact. Prints each chart by digest, one line each, for the release to attest.
#
#   scripts/release-charts.sh VERSION LOCK REGISTRY [helm push flags]
#   scripts/release-charts.sh 0.1.0 images.lock ghcr.io/coast-guide/charts
#   scripts/release-charts.sh 0.1.0-test images.lock 127.0.0.1:25000/charts --plain-http   (here)
set -eu
cd "$(dirname "$0")/.."
[ $# -ge 3 ] || { echo "usage: scripts/release-charts.sh VERSION LOCK REGISTRY [helm push flags]" >&2; exit 2; }
version=$1 lock=$(cd "$(dirname "$2")" && pwd)/$(basename "$2") registry=$3
shift 3
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

for chart in gen9-*/chart; do
  name=$(sed -n 's/^name: //p' "$chart/Chart.yaml")
  # A copy with the links resolved (compose.yaml and the files it mounts), the library at its
  # absolute path, and the lock inside
  cp -RL "$chart" "$work/$name"
  sed "s|file://../../deploy/helm/gen9-lib|file://$PWD/deploy/helm/gen9-lib|" "$chart/Chart.yaml" > "$work/$name/Chart.yaml"
  cp "$lock" "$work/$name/images.lock"
  helm package "$work/$name" --version "$version" --app-version "$version" --dependency-update -d "$work/out" >/dev/null 2>&1 ||
    { echo "$name: doesn't package" >&2; exit 1; }
  pushed=$(helm push "$work/out/$name-$version.tgz" "oci://$registry" "$@" 2>&1) ||
    { printf '%s: not pushed: %s\n' "$name" "$pushed" >&2; exit 1; }
  digest=$(printf '%s\n' "$pushed" | sed -n 's/^Digest: //p')
  echo "$registry/$name@$digest"
done
