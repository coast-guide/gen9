#!/usr/bin/env bash
# Check what Gen9 needs: Docker running, Docker Compose new enough, the tools the stacks' init-env.sh
# scripts use, the host ports of stacks that aren't running and (make doctor only) Docker's memory.
# Used by `make doctor`, and with --preflight by `make setup` and `make up`: then it prints only
# problems, so up fails before starting anything instead of halfway through a stack. make setup's
# adds --before-setup: what setup itself writes isn't checked yet.
#
#   scripts/doctor.sh [--preflight [--before-setup]] [STACK...]     STACK: postgres keycloak langfuse temporal models sandbox agent ui edge (default all)
#   scripts/doctor.sh [--preflight] --no-stacks     Docker, Compose, memory and the tools only (make's selection left every stack out)
set -euo pipefail
cd "$(dirname "$0")/.."

# Newest Compose feature a stack uses: env_file `required: false` (gen9-agent), Compose 2.24.0.
# https://docs.docker.com/compose/how-tos/environment-variables/set-environment-variables/
MIN_COMPOSE=2.24.0
# All stacks idle used 5.8 GiB with Docker Desktop, after a day of use (Langfuse 3.3,
# Keycloak 1.6, Temporal 0.4); Langfuse recommends 16 GiB for a server
# (https://langfuse.com/self-hosting/deployment/docker-compose).
MIN_MEMORY_MIB_LANGFUSE=8192

PREFLIGHT=false BEFORE_SETUP=false NO_STACKS=false STACKS=()
for arg in "$@"; do
  case $arg in
    --preflight) PREFLIGHT=true ;;
    --before-setup) BEFORE_SETUP=true ;;
    --no-stacks) NO_STACKS=true ;;
    -*) echo "unknown option: $arg" >&2; exit 2 ;;
    *) STACKS+=("$arg") ;;
  esac
done
[ ${#STACKS[@]} -gt 0 ] || $NO_STACKS || STACKS=(postgres keycloak langfuse temporal models sandbox agent ui edge)

failed=false
ok() { $PREFLIGHT || echo "ok    $*"; }
warn() { echo "warn  $*"; }
fail() { echo "FAIL  $*"; failed=true; }

# True when version $1 >= $2. Compares major.minor.patch numerically (sort -V is missing on older
# macOS); a missing part counts as 0 and a suffix such as -desktop.1 is ignored.
version_ge() {
  set -- "$(echo "$1.0.0" | cut -d. -f1-3)" "$(echo "$2.0.0" | cut -d. -f1-3)"
  [ "$(printf '%s\n%s\n' "$2" "$1" | sort -t. -k1,1n -k2,2n -k3,3n | head -1)" = "$2" ]
}

docker_ok=false
if ! command -v docker >/dev/null; then
  fail "docker not found: install Docker Desktop or Docker Engine (https://docs.docker.com/get-started/get-docker/)"
elif ! docker info >/dev/null 2>&1; then
  fail "Docker isn't running: start Docker Desktop (or the Docker service)"
else
  docker_ok=true
  ok "Docker $(docker version --format '{{.Server.Version}}') is running"
  compose=$(docker compose version --short 2>/dev/null || true)
  compose=${compose#v}
  if [ -z "$compose" ]; then
    fail "Docker Compose v2 not found (docker compose): https://docs.docker.com/compose/install/"
  elif ! version_ge "$compose" "$MIN_COMPOSE"; then
    fail "Docker Compose $compose is too old: Gen9 needs $MIN_COMPOSE or newer (https://docs.docker.com/compose/install/)"
  else
    ok "Docker Compose $compose"
  fi
fi

for tool in openssl od; do
  if command -v "$tool" >/dev/null; then ok "$tool"; else fail "$tool not found: make setup uses it to generate secrets"; fi
done

if $docker_ok; then
  if ! $PREFLIGHT && [[ " ${STACKS[*]} " == *" langfuse "* ]]; then
    mib=$(( $(docker info --format '{{.MemTotal}}') / 1048576 ))
    if [ "$mib" -lt "$MIN_MEMORY_MIB_LANGFUSE" ]; then
      warn "Docker has $mib MiB of memory; with gen9-langfuse give it at least $MIN_MEMORY_MIB_LANGFUSE (Docker Desktop: Settings > Resources)"
    else
      ok "Docker has $mib MiB of memory"
    fi
  fi

  # Host ports each stopped stack will publish, from its own Compose file and .env
  for stack in "${STACKS[@]}"; do
    if [ -n "$(docker ps -q --filter "label=com.docker.compose.project=gen9-$stack" --filter label=com.docker.compose.oneoff --filter status=running)" ]; then
      ok "gen9-$stack is running"
      continue
    fi
    if ! ports=$(cd "gen9-$stack" && docker compose config --format json 2>/dev/null); then
      ok "gen9-$stack isn't set up yet, so its ports aren't checked (make setup STACKS=$stack)"
      continue
    fi
    busy=()
    # Each port once: a port published for TCP and UDP (gen9-edge's 443, for HTTP/3) is listed twice
    for port in $(printf '%s' "$ports" | grep -o '"published": *"[0-9]*"' | grep -o '[0-9][0-9]*' | sort -un); do
      # Something answers on it: another program, since this stack isn't running
      if (: </dev/tcp/127.0.0.1/"$port") 2>/dev/null; then busy+=("$port"); fi
    done
    if [ ${#busy[@]} -eq 0 ]; then
      ok "gen9-$stack: its ports are free"
    else
      for port in "${busy[@]}"; do
        fail "gen9-$stack needs port $port, which another program uses (see: lsof -nP -iTCP:$port -sTCP:LISTEN)"
      done
    fi
  done
fi

# A bundled store left out of the stack's COMPOSE_PROFILES needs the setting its label gen9.external
# names, which points the stack at another one (docs/operations.md, "External services"); else what
# uses it starts with nothing to reach. The services Compose would start, and the environment it
# interpolates with (values only tested, never printed), from Compose itself
if $docker_ok && ! $BEFORE_SETUP; then
  for stack in "${STACKS[@]}"; do
    [ -f "gen9-$stack/.env" ] || continue
    # "store SETTING" for each service labelled so, from the stack's Compose files
    stores=$(awk '/^  [a-z][a-z0-9-]*:[[:space:]]*$/ { s = $1; sub(/:$/, "", s) }
      /^[[:space:]]+gen9\.external:/ { print s, $2 }' gen9-"$stack"/compose*.yaml)
    [ -n "$stores" ] || continue
    active=" $(cd "gen9-$stack" && docker compose config --services 2>/dev/null | tr '\n' ' ') "
    while read -r service setting; do
      [[ "$active" == *" $service "* ]] && continue
      # Its value, held here only: the host it names, which mustn't be the store's own service
      # (Langfuse's DATABASE_URL names the bundled postgres until it's changed)
      value=$(cd "gen9-$stack" && docker compose config --environment 2>/dev/null | sed -n "s/^$setting=//p" | tail -n 1)
      host=${value#*://}; host=${host##*@}; host=${host%%/*}; host=${host%%\?*}; host=${host%:*}
      if [ -n "$value" ] && [ "$host" != "$service" ]; then
        ok "gen9-$stack: $service is elsewhere ($setting)"
      else
        fail "gen9-$stack leaves out its $service (COMPOSE_PROFILES in gen9-$stack/.env), and $setting names no other: set $setting, or add $service to COMPOSE_PROFILES (docs/operations.md, \"External services\")"
      fi
    done <<<"$stores"
  done
fi

# gen9-agent reaches the database where gen9-postgres/.env says, as make setup copies it: a server
# named in one and not the other would leave gen9-agent at the wrong one
if ! $BEFORE_SETUP && [ -f gen9-postgres/.env ] && [ -f gen9-agent/.env ] &&
  [[ " ${STACKS[*]} " == *" agent "* || " ${STACKS[*]} " == *" postgres "* ]]; then
  for key in GEN9_POSTGRES_SERVER GEN9_POSTGRES_SERVER_PORT GEN9_POSTGRES_SSLMODE GEN9_POSTGRES_SSLROOTCERT; do
    if [ "$(sed -n "s/^$key=//p" gen9-postgres/.env | tail -n 1)" != "$(sed -n "s/^$key=//p" gen9-agent/.env | tail -n 1)" ]; then
      fail "gen9-agent/.env's $key differs from gen9-postgres/.env's: make setup STACKS=\"postgres agent\" copies it"
    fi
  done
fi

# Langfuse's own compose file (vendored, never edited) falls back to published defaults for its
# secrets (SALT mysalt, ENCRYPTION_KEY all zeros, …): a key missing from its .env must stop the
# start, not start Langfuse with a guessable value (M9, S2). init-env.sh writes every one of them
LANGFUSE_SECRETS="POSTGRES_PASSWORD CLICKHOUSE_PASSWORD REDIS_AUTH MINIO_ROOT_PASSWORD SALT ENCRYPTION_KEY NEXTAUTH_SECRET
  LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY LANGFUSE_S3_MEDIA_UPLOAD_SECRET_ACCESS_KEY LANGFUSE_S3_BATCH_EXPORT_SECRET_ACCESS_KEY"
if [[ " ${STACKS[*]} " == *" langfuse "* ]] && [ -f gen9-langfuse/.env ]; then
  missing=()
  for key in $LANGFUSE_SECRETS; do grep -Eq "^$key=.+" gen9-langfuse/.env || missing+=("$key"); done
  if [ ${#missing[@]} -eq 0 ]; then
    ok "gen9-langfuse/.env holds each of Langfuse's secrets"
  else
    fail "gen9-langfuse/.env has no ${missing[*]}: Langfuse would start with its compose file's published default. Put the value back (docs/secrets.md, or make restore); a new ENCRYPTION_KEY or SALT can't read what Langfuse stored"
  fi
fi

# Temporal's internode certificate (gen9-temporal/init-tls.sh): the stack stops working when it expires
tls_env=gen9-temporal/tls.local.env
if ! $PREFLIGHT && [[ " ${STACKS[*]} " == *" temporal "* ]] && [ -f "$tls_env" ] && command -v openssl >/dev/null; then
  cert=$(sed -n 's/^TEMPORAL_TLS_SERVER_CERT_DATA=//p' "$tls_env" | openssl base64 -d -A 2>/dev/null || true)
  until=$(printf '%s\n' "$cert" | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2 || true)
  if [ -z "$until" ]; then
    fail "$tls_env holds no readable certificate: gen9-temporal/init-tls.sh --force, then make up STACKS=temporal"
  elif ! printf '%s\n' "$cert" | openssl x509 -noout -checkend $((30 * 86400)) >/dev/null; then
    warn "Temporal's internode certificate expires $until: gen9-temporal/init-tls.sh --force, then make up STACKS=temporal"
  else
    ok "Temporal's internode certificate is valid until $until"
  fi
fi

# A deletion retries a failing step until it succeeds (gen9-agent's workflows/deletion.py), so one
# still running after a day has a step that keeps failing, such as Langfuse unreachable
if ! $PREFLIGHT && $docker_ok && [[ " ${STACKS[*]} " == *" temporal "* ]] &&
  [ -n "$(docker ps -q --filter label=com.docker.compose.project=gen9-temporal --filter label=com.docker.compose.service=temporal --filter status=running)" ]; then
  day_ago=$(date -u -v-1d +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d '1 day ago' +%Y-%m-%dT%H:%M:%SZ)
  stuck=$(docker compose -f gen9-temporal/compose.yaml run --rm -T cli temporal workflow count \
    --query "WorkflowType IN ('DeleteAccountWorkflow','DeleteThreadWorkflow') AND ExecutionStatus='Running' AND StartTime < '$day_ago'" 2>/dev/null |
    sed -n 's/^Total: //p' || true)
  if [ -z "$stuck" ]; then
    warn "couldn't ask Temporal about running deletions (docker compose -f gen9-temporal/compose.yaml run --rm cli temporal workflow count)"
  elif [ "$stuck" -gt 0 ]; then
    warn "$stuck deletion(s) running for over a day, retrying a step that keeps failing: Temporal's UI (http://localhost:18000, namespace gen9) shows each one's step and error (gen9-agent/README.md, Deletion)"
  else
    ok "no deletion has been running for over a day"
  fi
fi

if $failed; then
  $PREFLIGHT && echo "Fix the above, then rerun (make doctor checks everything)." >&2
  exit 1
fi
