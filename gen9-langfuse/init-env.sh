#!/usr/bin/env bash
# Generate the .env for this self-hosted Langfuse stack (docker-compose.yml + compose.override.yaml).
# Secrets are random; values that must match each other are derived from one source.
# Portable: bash 3.2+ (macOS and Linux), GNU or BusyBox tools; openssl optional. Run with --help.
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)

usage() {
  cat <<'EOF'
Generate .env for the self-hosted Langfuse stack.

Usage:
  ./init-env.sh --email EMAIL --name NAME [options]

Required:
  --email EMAIL            Login email of the initial user
  --name NAME              Display name of the initial user (must start with a letter)

Headless init (org, project, API keys, first user):
  --password PASS          Initial user password: 8+ chars with a letter, digit and special char  [generated]
  --org-id ID              Organization id (a-z, 0-9, -, _)             [gen9]
  --org-name NAME          Organization display name                    [Gen9]
  --project-id ID          Project id (a-z, 0-9, -, _)                  [gen9-agent]
  --project-name NAME      Project display name                         [gen9-agent]
  --public-key KEY         Project public key, must start with pk-lf-   [generated]
  --secret-key KEY         Project secret key, must start with sk-lf-   [generated]

Deployment:
  --url URL                Public URL of the UI and API (NEXTAUTH_URL)                 [http://localhost:<port>]
  --media-url URL          Public object storage URL, reachable by browsers AND SDKs   [http://localhost:<media-port>]
                           (also used for batch-export download links)
  --port N                 Host port for the UI and API (bound to 127.0.0.1)           [13000]
  --media-port N           Host port for object storage (S3 API)                       [13001]
  --console-port N         Host port for the MinIO console                             [13002]
  --worker-port N          Host port for the worker health check                       [13003]
  --postgres-port N        Host port for Postgres                                      [13004]
  --redis-port N           Host port for Redis                                         [13005]
  --clickhouse-http-port N Host port for ClickHouse HTTP                               [13006]
  --clickhouse-native-port N  Host port for ClickHouse native protocol                 [13007]
  --compose-project NAME   Compose project name; prefixes container and volume names  [gen9-langfuse]
  --telemetry true|false   Send aggregated usage telemetry to Langfuse                 [false]

Optional features:
  --smtp-url URL           SMTP connection URL for invites / password resets (e.g. smtp://user:pass@host:587)
  --email-from ADDRESS     Sender address for those emails (use together with --smtp-url)
  --batch-export           Enable batch exports of traces to object storage
  --experimental           Enable Langfuse experimental features

Anything else:
  --set KEY=VALUE          Set any other variable read by the compose files (repeatable)

Output:
  -o, --output FILE        File to write, or - for stdout                    [.env next to this script]
  --agent-env-file FILE    Also write LANGFUSE_BASE_URL + project keys for an app,
                           e.g. ../gen9-agent/langfuse.local.env
  --force                  Overwrite existing output files
  --from-env               Only write --agent-env-file, from the existing .env: generates nothing,
                           so the stack keeps working (for a lost or deleted settings file);
                           --email and --name aren't needed then
  -h, --help               Show this help

Values must not contain: $ " ` \ # or line breaks (they would be misread by Compose).
EOF
}

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
warn() { printf 'warning: %s\n' "$*" >&2; }
rand_hex() { # $1 = number of random bytes; prints 2*$1 hex chars
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex "$1"
  else
    od -An -vtx1 -N "$1" /dev/urandom | tr -d ' \n'
    echo
  fi
}

# ---- defaults -------------------------------------------------------------------------------
EMAIL="" NAME="" PASSWORD=""
ORG_ID="gen9" ORG_NAME="Gen9" PROJECT_ID="gen9-agent" PROJECT_NAME="gen9-agent"
PUBLIC_KEY="" SECRET_KEY=""
# Host ports (uncommon block, below the ephemeral ranges) and the public addresses Langfuse hands out
# (login redirects, links, media URLs). Unset URLs default to localhost on the chosen ports.
PORT="13000" MEDIA_PORT="13001" CONSOLE_PORT="13002" WORKER_PORT="13003"
POSTGRES_PORT="13004" REDIS_PORT="13005" CLICKHOUSE_HTTP_PORT="13006" CLICKHOUSE_NATIVE_PORT="13007"
URL="" MEDIA_URL=""
COMPOSE_PROJECT="gen9-langfuse" TELEMETRY="false"
SMTP_URL="" EMAIL_FROM="" BATCH_EXPORT="false" EXPERIMENTAL="false"
OUTPUT="$SCRIPT_DIR/.env" AGENT_ENV_FILE="" FORCE="false" FROM_ENV="false"
EXTRA_KEYS=() EXTRA_VALUES=()

write_private() { # $1=path; content on stdin. Written atomically with mode 600.
  local tmp
  tmp=$(umask 077 && mktemp "$(dirname "$1")/.init-env.XXXXXX")
  cat >"$tmp"
  chmod 600 "$tmp"
  mv -f "$tmp" "$1"
}

write_agent_file() {
  printf 'LANGFUSE_BASE_URL=%s\nLANGFUSE_PUBLIC_KEY=%s\nLANGFUSE_SECRET_KEY=%s\n' \
    "$URL" "$PUBLIC_KEY" "$SECRET_KEY" | write_private "$AGENT_ENV_FILE"
  printf 'wrote %s\n' "$AGENT_ENV_FILE"
}

# ---- parse flags (supports "--flag value" and "--flag=value") ---------------------------------
need_value() { [ "$2" -ge 2 ] || die "$1 needs a value"; }
while [ $# -gt 0 ]; do
  case "$1" in
    --*=*) flag=${1%%=*} value=${1#*=}; shift; set -- "$flag" "$value" "$@"; continue ;;
  esac
  case "$1" in
    --email) need_value "$1" $#; EMAIL=$2; shift 2 ;;
    --name) need_value "$1" $#; NAME=$2; shift 2 ;;
    --password) need_value "$1" $#; PASSWORD=$2; shift 2 ;;
    --org-id) need_value "$1" $#; ORG_ID=$2; shift 2 ;;
    --org-name) need_value "$1" $#; ORG_NAME=$2; shift 2 ;;
    --project-id) need_value "$1" $#; PROJECT_ID=$2; shift 2 ;;
    --project-name) need_value "$1" $#; PROJECT_NAME=$2; shift 2 ;;
    --public-key) need_value "$1" $#; PUBLIC_KEY=$2; shift 2 ;;
    --secret-key) need_value "$1" $#; SECRET_KEY=$2; shift 2 ;;
    --url) need_value "$1" $#; URL=$2; shift 2 ;;
    --media-url) need_value "$1" $#; MEDIA_URL=$2; shift 2 ;;
    --port) need_value "$1" $#; PORT=$2; shift 2 ;;
    --media-port) need_value "$1" $#; MEDIA_PORT=$2; shift 2 ;;
    --console-port) need_value "$1" $#; CONSOLE_PORT=$2; shift 2 ;;
    --worker-port) need_value "$1" $#; WORKER_PORT=$2; shift 2 ;;
    --postgres-port) need_value "$1" $#; POSTGRES_PORT=$2; shift 2 ;;
    --redis-port) need_value "$1" $#; REDIS_PORT=$2; shift 2 ;;
    --clickhouse-http-port) need_value "$1" $#; CLICKHOUSE_HTTP_PORT=$2; shift 2 ;;
    --clickhouse-native-port) need_value "$1" $#; CLICKHOUSE_NATIVE_PORT=$2; shift 2 ;;
    --compose-project) need_value "$1" $#; COMPOSE_PROJECT=$2; shift 2 ;;
    --telemetry) need_value "$1" $#; TELEMETRY=$2; shift 2 ;;
    --smtp-url) need_value "$1" $#; SMTP_URL=$2; shift 2 ;;
    --email-from) need_value "$1" $#; EMAIL_FROM=$2; shift 2 ;;
    --batch-export) BATCH_EXPORT="true"; shift ;;
    --experimental) EXPERIMENTAL="true"; shift ;;
    --set)
      need_value "$1" $#
      case "$2" in *=*) ;; *) die "--set expects KEY=VALUE, got: $2" ;; esac
      EXTRA_KEYS+=("${2%%=*}"); EXTRA_VALUES+=("${2#*=}"); shift 2 ;;
    -o | --output) need_value "$1" $#; OUTPUT=$2; shift 2 ;;
    --agent-env-file) need_value "$1" $#; AGENT_ENV_FILE=$2; shift 2 ;;
    --force) FORCE="true"; shift ;;
    --from-env) FROM_ENV="true"; shift ;;
    -h | --help) usage; exit 0 ;;
    *) die "unknown option: $1 (see --help)" ;;
  esac
done

# ---- --from-env: only the app's settings file, from the existing .env ---------------------------
if [ "$FROM_ENV" = "true" ]; then
  [ -f "$OUTPUT" ] || die "$OUTPUT doesn't exist: run without --from-env to create it"
  [ -n "$AGENT_ENV_FILE" ] || die "--from-env writes --agent-env-file; name it"
  if [ -e "$AGENT_ENV_FILE" ] && [ "$FORCE" != "true" ]; then die "$AGENT_ENV_FILE already exists; pass --force to overwrite"; fi
  env_value() { sed -n "s/^$1=//p" "$OUTPUT" | tail -n 1; }
  URL=$(env_value NEXTAUTH_URL) PUBLIC_KEY=$(env_value LANGFUSE_INIT_PROJECT_PUBLIC_KEY) SECRET_KEY=$(env_value LANGFUSE_INIT_PROJECT_SECRET_KEY)
  [ -n "$URL" ] && [ -n "$PUBLIC_KEY" ] && [ -n "$SECRET_KEY" ] ||
    die "$OUTPUT has no NEXTAUTH_URL, LANGFUSE_INIT_PROJECT_PUBLIC_KEY or LANGFUSE_INIT_PROJECT_SECRET_KEY"
  write_agent_file
  exit 0
fi

# ---- validation -----------------------------------------------------------------------------
[ -n "$EMAIL" ] || die "--email is required (see --help)"
[ -n "$NAME" ] || die "--name is required (see --help)"

check_safe() { # $1=label $2=value
  # shellcheck disable=SC1003 # '\' is intentionally a literal backslash
  case "$2" in
    *'$'* | *'"'* | *'`'* | *'\'* | *'#'* | *$'\n'* | *$'\r'*) die "$1 contains a character Compose would misread (\$ \" \` \\ # or line break)" ;;
  esac
}
check_url() { # $1=label $2=value
  [[ "$2" =~ ^https?://[^[:space:]]+$ ]] || die "$1 must start with http:// or https://, got: $2"
}
check_id() { # $1=label $2=value
  [[ "$2" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || die "$1 may only use a-z, 0-9, - and _ (got: $2)"
}

[[ "$EMAIL" =~ ^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$ ]] || die "--email is not a valid address: $EMAIL"
[[ "$NAME" =~ ^[[:alpha:]] ]] || die "--name must start with a letter"
check_id --org-id "$ORG_ID"
check_id --project-id "$PROJECT_ID"
check_id --compose-project "$COMPOSE_PROJECT"
PORT_FLAGS=("--port:$PORT" "--media-port:$MEDIA_PORT" "--console-port:$CONSOLE_PORT"
  "--worker-port:$WORKER_PORT" "--postgres-port:$POSTGRES_PORT" "--redis-port:$REDIS_PORT"
  "--clickhouse-http-port:$CLICKHOUSE_HTTP_PORT" "--clickhouse-native-port:$CLICKHOUSE_NATIVE_PORT")
for pair in "${PORT_FLAGS[@]}"; do
  value=${pair#*:}
  [[ "$value" =~ ^[0-9]+$ ]] && [ "$value" -ge 1 ] && [ "$value" -le 65535 ] || die "${pair%%:*} must be a port number (1-65535), got: $value"
done
[ "$(printf '%s\n' "${PORT_FLAGS[@]}" | cut -d: -f2 | sort -u | wc -l | tr -d ' ')" = "${#PORT_FLAGS[@]}" ] ||
  die "all host ports must be different: $(printf '%s ' "${PORT_FLAGS[@]}")"
[ -n "$URL" ] || URL="http://localhost:$PORT"
[ -n "$MEDIA_URL" ] || MEDIA_URL="http://localhost:$MEDIA_PORT"
check_url --url "$URL"
URL=${URL%/}
check_url --media-url "$MEDIA_URL"
MEDIA_URL=${MEDIA_URL%/}
case "$TELEMETRY" in true | false) ;; *) die "--telemetry must be true or false" ;; esac
if [ -n "$SMTP_URL" ]; then [[ "$SMTP_URL" =~ ^smtps?:// ]] || die "--smtp-url must start with smtp:// or smtps://"; fi
if [ -n "$EMAIL_FROM" ] && [ -z "$SMTP_URL" ]; then warn "--email-from has no effect without --smtp-url"; fi

if [ -n "$PASSWORD" ]; then
  # Mirrors Langfuse's signup password rules
  [ ${#PASSWORD} -ge 8 ] || die "--password must be at least 8 characters"
  [[ "$PASSWORD" =~ [A-Za-z] ]] || die "--password needs a letter"
  [[ "$PASSWORD" =~ [0-9] ]] || die "--password needs a digit"
  [[ "$PASSWORD" =~ [^A-Za-z0-9] ]] || die "--password needs a special character"
  [[ "$PASSWORD" =~ [[:space:]] ]] && die "--password must not contain spaces"
fi
if [ -n "$PUBLIC_KEY" ]; then [[ "$PUBLIC_KEY" =~ ^pk-lf-[A-Za-z0-9_-]+$ ]] || die "--public-key must look like pk-lf-..."; fi
if [ -n "$SECRET_KEY" ]; then [[ "$SECRET_KEY" =~ ^sk-lf-[A-Za-z0-9_-]+$ ]] || die "--secret-key must look like sk-lf-..."; fi

for pair in "--email:$EMAIL" "--name:$NAME" "--password:$PASSWORD" "--org-name:$ORG_NAME" \
  "--project-name:$PROJECT_NAME" "--smtp-url:$SMTP_URL" "--email-from:$EMAIL_FROM"; do
  check_safe "${pair%%:*}" "${pair#*:}"
done

# Keys this script manages itself; --set may not redefine them (use the dedicated flag instead)
MANAGED_KEYS=" COMPOSE_PROJECT_NAME COMPOSE_FILE NEXTAUTH_URL LANGFUSE_MEDIA_PUBLIC_URL
  LANGFUSE_PORT LANGFUSE_MEDIA_PORT LANGFUSE_MINIO_CONSOLE_PORT LANGFUSE_WORKER_PORT LANGFUSE_POSTGRES_PORT
  LANGFUSE_REDIS_PORT LANGFUSE_CLICKHOUSE_HTTP_PORT LANGFUSE_CLICKHOUSE_NATIVE_PORT
  LANGFUSE_S3_BATCH_EXPORT_EXTERNAL_ENDPOINT LANGFUSE_S3_BATCH_EXPORT_ENABLED TELEMETRY_ENABLED
  LANGFUSE_ENABLE_EXPERIMENTAL_FEATURES SMTP_CONNECTION_URL EMAIL_FROM_ADDRESS
  POSTGRES_PASSWORD DATABASE_URL CLICKHOUSE_PASSWORD REDIS_AUTH MINIO_ROOT_PASSWORD
  LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY LANGFUSE_S3_MEDIA_UPLOAD_SECRET_ACCESS_KEY
  LANGFUSE_S3_BATCH_EXPORT_SECRET_ACCESS_KEY SALT ENCRYPTION_KEY NEXTAUTH_SECRET
  LANGFUSE_INIT_ORG_ID LANGFUSE_INIT_ORG_NAME LANGFUSE_INIT_PROJECT_ID LANGFUSE_INIT_PROJECT_NAME
  LANGFUSE_INIT_PROJECT_PUBLIC_KEY LANGFUSE_INIT_PROJECT_SECRET_KEY LANGFUSE_INIT_USER_EMAIL
  LANGFUSE_INIT_USER_NAME LANGFUSE_INIT_USER_PASSWORD "
MANAGED_KEYS=$(printf '%s' "$MANAGED_KEYS" | tr -s '[:space:]' ' ')

# Variables the compose files actually read, so --set typos are caught
KNOWN_KEYS=" $(cat "$SCRIPT_DIR"/docker-compose.yml "$SCRIPT_DIR"/compose.override.yaml 2>/dev/null |
  grep -oE '\$\{[A-Z0-9_]+' | cut -c3- | sort -u | tr '\n' ' ') "

i=0
while [ $i -lt ${#EXTRA_KEYS[@]} ]; do
  key=${EXTRA_KEYS[$i]}
  [[ "$key" =~ ^[A-Z][A-Z0-9_]*$ ]] || die "--set key must be UPPER_SNAKE_CASE, got: $key"
  case "$MANAGED_KEYS" in *" $key "*) die "--set $key is managed by this script; use its dedicated flag" ;; esac
  [ "$key" != "POSTGRES_VERSION" ] || die "--set POSTGRES_VERSION has no effect: image versions are pinned in compose.override.yaml"
  case "$KNOWN_KEYS" in *" $key "*) ;; *) die "--set $key is not read by docker-compose.yml or compose.override.yaml (typo?)" ;; esac
  check_safe "--set $key" "${EXTRA_VALUES[$i]}"
  j=0
  while [ $j -lt $i ]; do
    [ "${EXTRA_KEYS[$j]}" != "$key" ] || die "--set $key given more than once"
    j=$((j + 1))
  done
  i=$((i + 1))
done

if [ "$OUTPUT" != "-" ] && [ -e "$OUTPUT" ] && [ "$FORCE" != "true" ]; then
  die "$OUTPUT already exists; pass --force to overwrite"
fi
if [ -n "$AGENT_ENV_FILE" ] && [ -e "$AGENT_ENV_FILE" ] && [ "$FORCE" != "true" ]; then
  die "$AGENT_ENV_FILE already exists; pass --force to overwrite"
fi

# Postgres only reads its password when its data volume is first created: new secrets would lock the
# stack out of its own data. Only generate for a fresh install (or when writing elsewhere, e.g. -o -).
if [ "$OUTPUT" = "$SCRIPT_DIR/.env" ] && command -v docker >/dev/null &&
  docker volume inspect "${COMPOSE_PROJECT}_langfuse_postgres_data" >/dev/null 2>&1; then
  die "volume ${COMPOSE_PROJECT}_langfuse_postgres_data exists; new secrets would not match it.
  change settings (ports, URLs) by editing .env, or start fresh (DELETES ALL DATA): docker compose down -v
  or, if only gen9-agent's settings file is gone: ./init-env.sh --from-env --agent-env-file FILE"
fi

# ---- generate -------------------------------------------------------------------------------
PG_PASSWORD=$(rand_hex 16)
MINIO_PASSWORD=$(rand_hex 16)
[ -n "$PASSWORD" ] || PASSWORD="Lf-$(rand_hex 12)-9"
[ -n "$PUBLIC_KEY" ] || PUBLIC_KEY="pk-lf-$(rand_hex 16)"
[ -n "$SECRET_KEY" ] || SECRET_KEY="sk-lf-$(rand_hex 32)"

render() {
  cat <<EOF
# Generated by init-env.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ). Do not commit.
# ---- Compose: which files to merge + stable project name (stable volume names)
COMPOSE_PROJECT_NAME=$COMPOSE_PROJECT
COMPOSE_FILE=docker-compose.yml:compose.override.yaml

# ---- Host ports (all bound to 127.0.0.1)
LANGFUSE_PORT=$PORT
LANGFUSE_MEDIA_PORT=$MEDIA_PORT
LANGFUSE_MINIO_CONSOLE_PORT=$CONSOLE_PORT
LANGFUSE_WORKER_PORT=$WORKER_PORT
LANGFUSE_POSTGRES_PORT=$POSTGRES_PORT
LANGFUSE_REDIS_PORT=$REDIS_PORT
LANGFUSE_CLICKHOUSE_HTTP_PORT=$CLICKHOUSE_HTTP_PORT
LANGFUSE_CLICKHOUSE_NATIVE_PORT=$CLICKHOUSE_NATIVE_PORT

# ---- Deployment
NEXTAUTH_URL=$URL
LANGFUSE_MEDIA_PUBLIC_URL=$MEDIA_URL
LANGFUSE_S3_BATCH_EXPORT_EXTERNAL_ENDPOINT=$MEDIA_URL
LANGFUSE_S3_BATCH_EXPORT_ENABLED=$BATCH_EXPORT
TELEMETRY_ENABLED=$TELEMETRY
LANGFUSE_ENABLE_EXPERIMENTAL_FEATURES=$EXPERIMENTAL
EOF
  [ -z "$SMTP_URL" ] || echo "SMTP_CONNECTION_URL=$SMTP_URL"
  [ -z "$EMAIL_FROM" ] || echo "EMAIL_FROM_ADDRESS=$EMAIL_FROM"
  cat <<EOF

# ---- Infrastructure secrets (values that must match are derived from one source)
POSTGRES_PASSWORD=$PG_PASSWORD
DATABASE_URL=postgresql://postgres:$PG_PASSWORD@postgres:5432/postgres
CLICKHOUSE_PASSWORD=$(rand_hex 16)
REDIS_AUTH=$(rand_hex 16)
MINIO_ROOT_PASSWORD=$MINIO_PASSWORD
LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY=$MINIO_PASSWORD
LANGFUSE_S3_MEDIA_UPLOAD_SECRET_ACCESS_KEY=$MINIO_PASSWORD
LANGFUSE_S3_BATCH_EXPORT_SECRET_ACCESS_KEY=$MINIO_PASSWORD
SALT=$(rand_hex 16)
ENCRYPTION_KEY=$(rand_hex 32)
NEXTAUTH_SECRET=$(rand_hex 32)

# ---- Headless init: org, project, API keys, first user (values must not be double-quoted)
LANGFUSE_INIT_ORG_ID=$ORG_ID
LANGFUSE_INIT_ORG_NAME=$ORG_NAME
LANGFUSE_INIT_PROJECT_ID=$PROJECT_ID
LANGFUSE_INIT_PROJECT_NAME=$PROJECT_NAME
LANGFUSE_INIT_PROJECT_PUBLIC_KEY=$PUBLIC_KEY
LANGFUSE_INIT_PROJECT_SECRET_KEY=$SECRET_KEY
LANGFUSE_INIT_USER_EMAIL=$EMAIL
LANGFUSE_INIT_USER_NAME=$NAME
LANGFUSE_INIT_USER_PASSWORD=$PASSWORD
EOF
  if [ ${#EXTRA_KEYS[@]} -gt 0 ]; then
    printf '\n# ---- Extra settings (--set)\n'
    i=0
    while [ $i -lt ${#EXTRA_KEYS[@]} ]; do
      printf '%s=%s\n' "${EXTRA_KEYS[$i]}" "${EXTRA_VALUES[$i]}"
      i=$((i + 1))
    done
  fi
}

if [ "$OUTPUT" = "-" ]; then
  render
else
  render | write_private "$OUTPUT"
  printf 'wrote %s\n' "$OUTPUT"
fi

[ -z "$AGENT_ENV_FILE" ] || write_agent_file

if [ "$OUTPUT" != "-" ]; then
  cat >&2 <<EOF

Next:
  UI:        $URL   (login: $EMAIL)
  Password:  grep ^LANGFUSE_INIT_USER_PASSWORD= $OUTPUT
  Validate:  docker compose config --quiet
  Start:     docker compose up -d
EOF
fi
