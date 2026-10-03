#!/bin/sh
# A release's Compose bundle (docs/plans/deploy.md, U7): the repository at the release's commit
# (git archive) with its images.lock beside the Makefile, so on any machine with Docker
#   tar xzf gen9-X.tar.gz && cd gen9-X && make setup && make up IMAGES=images.lock
# runs that release's images by digest, building nothing. Prints the bundle's path.
#
#   scripts/release-bundle.sh VERSION LOCK OUTDIR [COMMIT]     COMMIT: HEAD by default
set -eu
cd "$(dirname "$0")/.."
[ $# -ge 3 ] || { echo "usage: scripts/release-bundle.sh VERSION LOCK OUTDIR [COMMIT]" >&2; exit 2; }
version=$1 lock=$2 out=$3 commit=${4:-HEAD}
name=gen9-$version
mkdir -p "$out"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

git archive --format=tar --prefix="$name/" -o "$work/$name.tar" "$commit"
mkdir -p "$work/$name"
cp "$lock" "$work/$name/images.lock"
# The lock's entry as git archive writes the others: the commit's time, no owner (GNU tar's options)
when=$(git log -1 --format=%ct "$commit")
tar -rf "$work/$name.tar" -C "$work" --mtime="@$when" --owner=0 --group=0 --numeric-owner --mode=0644 "$name/images.lock"
# -n: no name or time in the gzip header, so the same commit and lock make the same file
gzip -n -9 -c "$work/$name.tar" > "$out/$name.tar.gz"
echo "$out/$name.tar.gz"
