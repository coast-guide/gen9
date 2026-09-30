#!/usr/bin/env bash
# The seeded admin's authenticator code, for signing in as them by hand: admins need a second step,
# and their app's secret (GEN9_SEED_ADMIN_OTP_SECRET in gen9-keycloak/.env, which configure.sh gives
# them) isn't in the form an authenticator app takes. RFC 6238 as Keycloak computes it: HMAC-SHA1
# keyed by the secret's own bytes, 30 s windows, 6 digits. Portable: bash 3.2+ and openssl.
set -euo pipefail
cd "$(dirname "$0")/.."

env_file=gen9-keycloak/.env
[ -f "$env_file" ] || { echo "No $env_file yet: run make setup" >&2; exit 1; }
secret=$(sed -n 's/^GEN9_SEED_ADMIN_OTP_SECRET=//p' "$env_file")
[ -n "$secret" ] || {
  echo "No GEN9_SEED_ADMIN_OTP_SECRET in $env_file: make setup STACKS=keycloak adds it, make up STACKS=keycloak gives it to the admin" >&2
  exit 1
}
email=$(sed -n 's/^GEN9_SEED_ADMIN_EMAIL=//p' "$env_file")

now=$(date +%s)
# The window as 8 bytes, big-endian, written by printf's \x escapes
counter=$(printf '%016x' $((now / 30)) | sed 's/../\\x&/g')
# shellcheck disable=SC2059 # the format is the escapes themselves
hash=$(printf "$counter" | openssl dgst -sha1 -hmac "$secret" -binary | od -An -vtx1 | tr -d ' \n')
offset=$((16#${hash:39:1}))
code=$(((16#${hash:$((offset * 2)):8} & 0x7fffffff) % 1000000))
printf '%s: %06d (for %d s more; a code works once)\n' "${email:-The seeded admin}" "$code" $((30 - now % 30))
