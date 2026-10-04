#!/usr/bin/env bash
# Generate the .env for gen9-keycloak: database + bootstrap admin passwords, the gen9-ui client
# secret and passwords for the two seeded local users. Optionally write the settings the apps need.
# Portable: bash 3.2+ (macOS and Linux). Run with --help.
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)

usage() {
  cat <<'USAGE'
Generate .env for the gen9-keycloak stack.

Usage: ./init-env.sh [options]

Deployment:
  --url URL               Public Keycloak URL = token issuer base (KC_HOSTNAME)  [http://localhost:<port>]
  --port N                Host port for Keycloak (bound to 127.0.0.1)           [15000]
  --ui-url URL            gen9-ui production URL                                [http://localhost:14000]
  --ui-dev-url URL        gen9-ui development URL                               [http://localhost:14001]
  --temporal-ui-url URL   Temporal's web UI (gen9-temporal)                     [http://localhost:18000]

Seeded local users (first start only):
  --admin-email EMAIL     Admin user (groups: users, admins)                    [ada@gen9.test]
  --user-email EMAIL      Regular user (group: users)                           [alan@gen9.test]

Output:
  -o, --output FILE       File to write, or - for stdout                        [.env next to this script]
  --ui-env-file FILE      Also write the OIDC settings for gen9-ui, e.g. ../gen9-ui/keycloak.local.env
  --agent-env-file FILE   Also write the token-validation settings for gen9-agent,
                          e.g. ../gen9-agent/keycloak.local.env
  --temporal-env-file FILE
                          Also write the sign-in settings for Temporal's web UI,
                          e.g. ../gen9-temporal/keycloak.local.env
  --force                 Overwrite existing output files
  --from-env              Only write --ui-env-file / --agent-env-file / --temporal-env-file, from the existing .env:
                          generates nothing, so users and clients keep working (for a lost or
                          deleted settings file)
  -h, --help              Show this help
USAGE
}

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
rand_hex() { if command -v openssl >/dev/null 2>&1; then openssl rand -hex "$1"; else od -An -vtx1 -N "$1" /dev/urandom | tr -d ' \n'; echo; fi; }
# Human-typeable, 29 chars: satisfies the realm policy (15+ chars, not in the NCSC blocklist)
user_password() { local h; h=$(rand_hex 12); printf 'gen9-%s-%s-%s-%s\n' "${h:0:6}" "${h:6:6}" "${h:12:6}" "${h:18:6}"; }

URL="" PORT="15000" UI_URL="http://localhost:14000" UI_DEV_URL="http://localhost:14001"
TEMPORAL_UI_URL="http://localhost:18000"
ADMIN_EMAIL="ada@gen9.test" USER_EMAIL="alan@gen9.test"
OUTPUT="$SCRIPT_DIR/.env" UI_ENV_FILE="" AGENT_ENV_FILE="" TEMPORAL_ENV_FILE="" FORCE="false" FROM_ENV="false"

write_private() {
  local tmp
  tmp=$(umask 077 && mktemp "$(dirname "$1")/.init-env.XXXXXX")
  cat >"$tmp" && chmod 600 "$tmp" && mv -f "$tmp" "$1"
  printf 'wrote %s
' "$1"
}

# Settings files for the apps: host-side URLs (each app's compose.yaml sets KEYCLOAK_INTERNAL_URL
# for its containers) and their client secrets
write_app_files() {
  if [ -n "$UI_ENV_FILE" ]; then
    printf '# gen9-keycloak settings for gen9-ui (generated %s)\nKEYCLOAK_ISSUER=%s\nKEYCLOAK_CLIENT_ID=gen9-ui\nKEYCLOAK_CLIENT_SECRET=%s\n' \
      "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$ISSUER" "$CLIENT_SECRET" | write_private "$UI_ENV_FILE"
  fi
  if [ -n "$AGENT_ENV_FILE" ]; then
    printf '# gen9-keycloak settings for gen9-agent (generated %s)\nKEYCLOAK_ISSUER=%s\nKEYCLOAK_AUDIENCE=gen9-agent\nKEYCLOAK_ADMIN_CLIENT_ID=gen9-agent\nKEYCLOAK_ADMIN_CLIENT_SECRET=%s\n' \
      "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$ISSUER" "$AGENT_CLIENT_SECRET" | write_private "$AGENT_ENV_FILE"
  fi
  if [ -n "$TEMPORAL_ENV_FILE" ]; then
    # Temporal's web UI signs in as client temporal-ui (config/configure.sh creates it); its
    # compose.yaml reaches Keycloak at gen9-keycloak:8080 and names this issuer for browsers
    printf '# gen9-keycloak settings for gen9-temporal (generated %s)\nTEMPORAL_AUTH_ISSUER_URL=%s\nTEMPORAL_AUTH_CLIENT_ID=temporal-ui\nTEMPORAL_AUTH_CLIENT_SECRET=%s\n' \
      "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$ISSUER" "$TEMPORAL_UI_CLIENT_SECRET" | write_private "$TEMPORAL_ENV_FILE"
  fi
}

while [ $# -gt 0 ]; do
  case "$1" in
    --*=*) flag=${1%%=*} value=${1#*=}; shift; set -- "$flag" "$value" "$@"; continue ;;
  esac
  case "$1" in
    --url) [ $# -ge 2 ] || die "$1 needs a value"; URL=$2; shift 2 ;;
    --port) [ $# -ge 2 ] || die "$1 needs a value"; PORT=$2; shift 2 ;;
    --ui-url) [ $# -ge 2 ] || die "$1 needs a value"; UI_URL=$2; shift 2 ;;
    --ui-dev-url) [ $# -ge 2 ] || die "$1 needs a value"; UI_DEV_URL=$2; shift 2 ;;
    --temporal-ui-url) [ $# -ge 2 ] || die "$1 needs a value"; TEMPORAL_UI_URL=$2; shift 2 ;;
    --admin-email) [ $# -ge 2 ] || die "$1 needs a value"; ADMIN_EMAIL=$2; shift 2 ;;
    --user-email) [ $# -ge 2 ] || die "$1 needs a value"; USER_EMAIL=$2; shift 2 ;;
    -o | --output) [ $# -ge 2 ] || die "$1 needs a value"; OUTPUT=$2; shift 2 ;;
    --ui-env-file) [ $# -ge 2 ] || die "$1 needs a value"; UI_ENV_FILE=$2; shift 2 ;;
    --agent-env-file) [ $# -ge 2 ] || die "$1 needs a value"; AGENT_ENV_FILE=$2; shift 2 ;;
    --temporal-env-file) [ $# -ge 2 ] || die "$1 needs a value"; TEMPORAL_ENV_FILE=$2; shift 2 ;;
    --force) FORCE="true"; shift ;;
    --from-env) FROM_ENV="true"; shift ;;
    -h | --help) usage; exit 0 ;;
    *) die "unknown option: $1 (see --help)" ;;
  esac
done

if [ "$FROM_ENV" = "true" ]; then
  [ -f "$OUTPUT" ] || die "$OUTPUT doesn't exist: run without --from-env to create it"
  [ -n "$UI_ENV_FILE$AGENT_ENV_FILE$TEMPORAL_ENV_FILE" ] || die "--from-env writes --ui-env-file, --agent-env-file and --temporal-env-file; name one"
  env_value() { sed -n "s/^$1=//p" "$OUTPUT" | tail -n 1; }
  URL=$(env_value KC_HOSTNAME) CLIENT_SECRET=$(env_value GEN9_UI_CLIENT_SECRET) AGENT_CLIENT_SECRET=$(env_value GEN9_AGENT_CLIENT_SECRET)
  [ -n "$URL" ] && [ -n "$CLIENT_SECRET" ] && [ -n "$AGENT_CLIENT_SECRET" ] ||
    die "$OUTPUT has no KC_HOSTNAME, GEN9_UI_CLIENT_SECRET or GEN9_AGENT_CLIENT_SECRET"
  TEMPORAL_UI_CLIENT_SECRET=$(env_value GEN9_TEMPORAL_UI_CLIENT_SECRET)
  [ -z "$TEMPORAL_ENV_FILE" ] || [ -n "$TEMPORAL_UI_CLIENT_SECRET" ] ||
    die "$OUTPUT has no GEN9_TEMPORAL_UI_CLIENT_SECRET (make setup STACKS=keycloak adds it)"
  ISSUER="$URL/realms/gen9"
  for f in "$UI_ENV_FILE" "$AGENT_ENV_FILE" "$TEMPORAL_ENV_FILE"; do
    if [ -n "$f" ] && [ -e "$f" ] && [ "$FORCE" != "true" ]; then die "$f already exists; pass --force to overwrite"; fi
  done
  write_app_files
  exit 0
fi

[[ "$PORT" =~ ^[0-9]+$ ]] && [ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ] || die "--port must be 1-65535"
[ -n "$URL" ] || URL="http://localhost:$PORT"
for pair in "--url:$URL" "--ui-url:$UI_URL" "--ui-dev-url:$UI_DEV_URL" "--temporal-ui-url:$TEMPORAL_UI_URL"; do
  [[ "${pair#*:}" =~ ^https?://[^[:space:]/]+$ ]] || die "${pair%%:*} must be http(s)://host[:port] without a path, got: ${pair#*:}"
done
for pair in "--admin-email:$ADMIN_EMAIL" "--user-email:$USER_EMAIL"; do
  [[ "${pair#*:}" =~ ^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$ ]] || die "${pair%%:*} is not a valid address"
done
[ "$ADMIN_EMAIL" != "$USER_EMAIL" ] || die "--admin-email and --user-email must differ"
for f in "$OUTPUT" "$UI_ENV_FILE" "$AGENT_ENV_FILE" "$TEMPORAL_ENV_FILE"; do
  if [ -n "$f" ] && [ "$f" != "-" ] && [ -e "$f" ] && [ "$FORCE" != "true" ]; then die "$f already exists; pass --force to overwrite"; fi
done
# The realm (client secret, seeded users) and the DB password are applied only on first start
if [ "$OUTPUT" = "$SCRIPT_DIR/.env" ] && command -v docker >/dev/null &&
  docker volume inspect gen9-keycloak_postgres_data >/dev/null 2>&1; then
  die "volume gen9-keycloak_postgres_data exists; new secrets would not match it.
  start fresh (DELETES ALL USERS): docker compose down -v
  or, if only an app's settings file is gone: ./init-env.sh --from-env --ui-env-file/--agent-env-file FILE"
fi

CLIENT_SECRET=$(rand_hex 32)
AGENT_CLIENT_SECRET=$(rand_hex 32)
TEMPORAL_UI_CLIENT_SECRET=$(rand_hex 32)
ISSUER="$URL/realms/gen9"

render() {
  cat <<ENV
# Generated by init-env.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ). Do not commit.
COMPOSE_PROJECT_NAME=gen9-keycloak
# The bundled services this stack runs. Without postgres, KC_DB_URL_HOST names another server
# (docs/operations.md, "External services")
COMPOSE_PROFILES=postgres
KEYCLOAK_PORT=$PORT
KC_HOSTNAME=$URL
KC_DB_PASSWORD=$(rand_hex 16)
KC_BOOTSTRAP_ADMIN_USERNAME=admin
KC_BOOTSTRAP_ADMIN_PASSWORD=$(rand_hex 16)
# Mailpit's web UI and API (user gen9): it holds every email, password resets included
MAILPIT_UI_PASSWORD=$(rand_hex 16)

# Realm import (first start only): gen9-ui client + seeded local users
GEN9_UI_URL=$UI_URL
GEN9_UI_DEV_URL=$UI_DEV_URL
GEN9_UI_BACKCHANNEL_LOGOUT_URL=http://gen9-ui:3000/auth/backchannel-logout
GEN9_UI_CLIENT_SECRET=$CLIENT_SECRET
GEN9_AGENT_CLIENT_SECRET=$AGENT_CLIENT_SECRET
# Temporal's web UI (config/configure.sh keeps its client in line on every start)
GEN9_TEMPORAL_UI_URL=$TEMPORAL_UI_URL
GEN9_TEMPORAL_UI_CLIENT_SECRET=$TEMPORAL_UI_CLIENT_SECRET
GEN9_SEED_ADMIN_EMAIL=$ADMIN_EMAIL
GEN9_SEED_ADMIN_PASSWORD=$(user_password)
# The seeded admin's authenticator app (config/configure.sh gives it to them): admins need a second step
GEN9_SEED_ADMIN_OTP_SECRET=$(rand_hex 20)
GEN9_SEED_USER_EMAIL=$USER_EMAIL
GEN9_SEED_USER_PASSWORD=$(user_password)
ENV
}

if [ "$OUTPUT" = "-" ]; then render; else render | write_private "$OUTPUT"; fi
write_app_files

if [ "$OUTPUT" != "-" ]; then
  cat >&2 <<NEXT

Next:
  docker compose build && docker compose up -d --wait
  Issuer:        $ISSUER
  Admin console: $URL/admin   (user: admin, password: grep ^KC_BOOTSTRAP_ADMIN_PASSWORD= .env)
  Local users:   $ADMIN_EMAIL / $USER_EMAIL   (grep ^GEN9_SEED_ .env)
  Mail catcher:  http://localhost:15002
NEXT
fi
