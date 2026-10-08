#!/usr/bin/env bash
# Generate what each stack needs before its first start: secrets, seeded users and the settings
# files apps read from each other. Used by `make setup`. Skips whatever already exists and never
# overwrites or deletes anything (start over with `make distclean`), with one exception: a provider
# key left in gen9-agent/.env from before the model router moves to gen9-models/.env.
#
#   scripts/setup.sh [STACK...]      default: postgres keycloak langfuse temporal models sandbox agent ui edge
#
# DOMAIN=gen9.example.com serves Gen9 under that domain, over TLS: gen9-edge set up (optional
# otherwise), EDGE_TLS its certificate (`internal`, Caddy's own CA, the default; or an email, for
# Let's Encrypt), and every address browsers and terminals use, in each stack's settings files,
# one host per service under it (gen9-edge/README.md). DOMAIN=localhost goes back to the ports.
#
# gen9-langfuse's first user comes from LANGFUSE_EMAIL and LANGFUSE_NAME, or is asked for. Its
# project keys go to gen9-agent/langfuse.local.env, which gen9-agent reads before its .env.
# gen9-models' provider keys come from OPENAI_API_KEY and OPENROUTER_API_KEY, or are asked for
# (hidden) when there is a terminal: `chat` needs OpenRouter's, speech and images OpenAI's.
set -euo pipefail
cd "$(dirname "$0")/.."
# Each stack's init-env.sh prints its own "Next:" (docker compose up, its addresses) when run alone;
# under make setup, make up starts them, lists the addresses, and this script ends with the sign-ins
export GEN9_SETUP=1

ALL=(postgres keycloak langfuse temporal models sandbox agent ui edge)
[ $# -gt 0 ] || set -- "${ALL[@]}"
for stack in "$@"; do
  case " ${ALL[*]} " in *" $stack "*) ;; *) echo "unknown stack: $stack (${ALL[*]})" >&2; exit 2 ;; esac
done
ARGS=" $* "
selected() { [[ "$ARGS" == *" $1 "* ]]; }

die() { echo "error: $*" >&2; exit 1; }
kept() { echo "  $1: already set up, kept"; }

# Set KEY=VALUE in FILE in place of KEY's line (added if there is none): written atomically and
# private, like the generated files, and the value never passes through sed or awk
set_env() {
  local file=$1 key=$2 value=$3 tmp line found=false
  tmp=$(umask 077 && mktemp "$(dirname "$file")/.setup.XXXXXX")
  while IFS= read -r line || [ -n "$line" ]; do
    case $line in
      "$key="*) printf '%s=%s\n' "$key" "$value"; found=true ;;
      *) printf '%s\n' "$line" ;;
    esac
  done <"$file" >"$tmp"
  $found || printf '%s=%s\n' "$key" "$value" >>"$tmp"
  mv -f "$tmp" "$file"
}

# Remove KEY's line from FILE, written atomically and private like set_env
unset_env() {
  local file=$1 key=$2 tmp line
  tmp=$(umask 077 && mktemp "$(dirname "$file")/.setup.XXXXXX")
  while IFS= read -r line || [ -n "$line" ]; do
    case $line in "$key="*) ;; *) printf '%s\n' "$line" ;; esac
  done <"$file" >"$tmp"
  mv -f "$tmp" "$file"
}

# The host a setting names: a URL's (its scheme, user and password, port and path dropped), or
# the value itself. A store's setting that names the store's own service names no other
host_of() { local v=${1#*://}; v=${v##*@}; v=${v%%/*}; v=${v%%\?*}; printf '%s\n' "${v%:*}"; }

# A bundled store (a Compose profile named after its service) in FILE's COMPOSE_PROFILES, as
# init-env.sh writes it, unless FILE's SETTING points the stack at another one (docs/operations.md,
# "External services"). An .env from before the stores were profiles (no COMPOSE_PROFILES) gets
# it in any case, as every store ran then; other profiles are kept
bundled() {
  local file=$1 store=$2 setting=$3 profiles value
  [ -f "$file" ] || return 0
  profiles=$(sed -n 's/^COMPOSE_PROFILES=//p' "$file" | tail -n 1)
  case ",$profiles," in *",$store,"*) return 0 ;; esac
  value=$(sed -n "s/^$setting=//p" "$file" | tail -n 1)
  if grep -q '^COMPOSE_PROFILES=' "$file" && [ -n "$value" ] && [ "$(host_of "$value")" != "$store" ]; then return 0; fi
  set_env "$file" COMPOSE_PROFILES "${profiles:+$profiles,}$store"
  echo "  $file: COMPOSE_PROFILES now lists $store, the bundled one ($setting names no other)"
}

# No usable OpenAI key in gen9-agent/.env: missing, empty, or sample.env's placeholder
# A provider key for gen9-models: from the environment (scripts and CI), else asked for when
# there is a terminal (hidden, Enter skips), else left for you to add. The value is never printed
provider_key() {
  local key=$1 what=$2 value=${!1:-}
  if [ -n "$value" ]; then
    set_env gen9-models/.env "$key" "$value" && echo "  $key taken from the environment, saved to gen9-models/.env"
  elif [ -t 0 ]; then
    printf '  %s for gen9-models (%s; typing is hidden, Enter skips): ' "$key" "$what"
    read -rs value || value=""
    echo
    if [ -n "$value" ]; then set_env gen9-models/.env "$key" "$value" && echo "  saved to gen9-models/.env"; fi
  fi
  grep -Eq "^$key=.+" gen9-models/.env ||
    echo "  add your $key to gen9-models/.env ($what): the router needs it"
}

no_openai_key() { ! grep -Eq '^OPENAI_API_KEY=.+' gen9-agent/.env || grep -q '^OPENAI_API_KEY=sk-\.\.\.$' gen9-agent/.env; }

# Langfuse's first user, asked for before anything is generated so a missing answer leaves no half setup
if selected langfuse && [ ! -f gen9-langfuse/.env ]; then
  if [ -z "${LANGFUSE_EMAIL:-}" ] || [ -z "${LANGFUSE_NAME:-}" ]; then
    [ -t 0 ] || die 'gen9-langfuse needs its first user: add LANGFUSE_EMAIL=you@example.com LANGFUSE_NAME="Your Name"'
    echo "gen9-langfuse needs its first user (you sign in to http://localhost:13000 with it)."
    [ -n "${LANGFUSE_EMAIL:-}" ] || { printf '  Email: '; read -r LANGFUSE_EMAIL; }
    [ -n "${LANGFUSE_NAME:-}" ] || { printf '  Name: '; read -r LANGFUSE_NAME; }
    [ -n "$LANGFUSE_EMAIL" ] && [ -n "$LANGFUSE_NAME" ] || die "both are needed; nothing was generated"
  fi
fi

if selected postgres; then
  echo "gen9-postgres"
  if [ -f gen9-postgres/.env ]; then
    kept gen9-postgres/.env
    bundled gen9-postgres/.env postgres GEN9_POSTGRES_SERVER
    # An .env from before gen9-agent's services had a role of their own: add its
    # password; gen9-postgres creates the role on its next start
    if ! grep -Eq '^GEN9_AGENT_APP_DB_PASSWORD=.+' gen9-postgres/.env; then
      set_env gen9-postgres/.env GEN9_AGENT_APP_DB_PASSWORD "$(openssl rand -hex 16)"
      echo "  added the password of gen9-agent's services' role (gen9_agent_app) to gen9-postgres/.env"
    fi
    # A settings file written for another stack is rebuilt from the .env, never regenerated
    rebuild=()
    [ -f gen9-agent/postgres.local.env ] || rebuild+=(--agent-env-file gen9-agent/postgres.local.env)
    [ -f gen9-agent/postgres-app.local.env ] || rebuild+=(--agent-app-env-file gen9-agent/postgres-app.local.env)
    [ ${#rebuild[@]} -eq 0 ] || gen9-postgres/init-env.sh --from-env "${rebuild[@]}"
  else
    gen9-postgres/init-env.sh --agent-env-file gen9-agent/postgres.local.env \
      --agent-app-env-file gen9-agent/postgres-app.local.env
  fi
fi

if selected keycloak; then
  echo "gen9-keycloak"
  if [ -f gen9-keycloak/.env ]; then
    kept gen9-keycloak/.env
    bundled gen9-keycloak/.env postgres KC_DB_URL_HOST
    # An .env from before Temporal's web UI signed in through Keycloak: add its client (configure.sh
    # creates it on the next start)
    if ! grep -Eq '^GEN9_TEMPORAL_UI_CLIENT_SECRET=.+' gen9-keycloak/.env; then
      set_env gen9-keycloak/.env GEN9_TEMPORAL_UI_URL "http://localhost:18000"
      set_env gen9-keycloak/.env GEN9_TEMPORAL_UI_CLIENT_SECRET "$(openssl rand -hex 32)"
      echo "  added the temporal-ui client secret to gen9-keycloak/.env"
    fi
    # Admins need a second step: the seeded admin's authenticator app (configure.sh gives it to them)
    if ! grep -Eq '^GEN9_SEED_ADMIN_OTP_SECRET=.+' gen9-keycloak/.env; then
      set_env gen9-keycloak/.env GEN9_SEED_ADMIN_OTP_SECRET "$(openssl rand -hex 20)"
      echo "  added the seeded admin's authenticator secret to gen9-keycloak/.env"
    fi
    # Mailpit's web UI and API ask for a password (user gen9)
    if ! grep -Eq '^MAILPIT_UI_PASSWORD=.+' gen9-keycloak/.env; then
      set_env gen9-keycloak/.env MAILPIT_UI_PASSWORD "$(openssl rand -hex 16)"
      echo "  added Mailpit's password (user gen9) to gen9-keycloak/.env"
    fi
    rebuild=()
    [ -f gen9-ui/keycloak.local.env ] || rebuild+=(--ui-env-file gen9-ui/keycloak.local.env)
    [ -f gen9-agent/keycloak.local.env ] || rebuild+=(--agent-env-file gen9-agent/keycloak.local.env)
    [ -f gen9-temporal/keycloak.local.env ] || rebuild+=(--temporal-env-file gen9-temporal/keycloak.local.env)
    [ ${#rebuild[@]} -eq 0 ] || gen9-keycloak/init-env.sh --from-env "${rebuild[@]}"
  else
    gen9-keycloak/init-env.sh --ui-env-file gen9-ui/keycloak.local.env --agent-env-file gen9-agent/keycloak.local.env \
      --temporal-env-file gen9-temporal/keycloak.local.env
  fi
fi

if selected langfuse; then
  echo "gen9-langfuse"
  if [ -f gen9-langfuse/.env ]; then
    kept gen9-langfuse/.env
    bundled gen9-langfuse/.env postgres DATABASE_URL
    bundled gen9-langfuse/.env redis REDIS_HOST
    bundled gen9-langfuse/.env clickhouse CLICKHOUSE_URL
    bundled gen9-langfuse/.env minio LANGFUSE_S3_EVENT_UPLOAD_ENDPOINT
  else
    gen9-langfuse/init-env.sh --email "$LANGFUSE_EMAIL" --name "$LANGFUSE_NAME" \
      --agent-env-file gen9-agent/langfuse.local.env
  fi
fi

if selected temporal; then
  echo "gen9-temporal"
  if [ -f gen9-temporal/.env ]; then kept gen9-temporal/.env; bundled gen9-temporal/.env postgres POSTGRES_SEEDS; else gen9-temporal/init-env.sh; fi
  # The internode certificate; an install from before it existed gets one here
  if [ -f gen9-temporal/tls.local.env ]; then kept gen9-temporal/tls.local.env; else gen9-temporal/init-tls.sh; fi
fi

if selected sandbox; then
  echo "gen9-sandbox"
  if [ -f gen9-sandbox/.env ]; then
    kept gen9-sandbox/.env
    [ -f gen9-agent/sandbox.local.env ] ||
      gen9-sandbox/init-env.sh --from-env --agent-env-file gen9-agent/sandbox.local.env
  else
    gen9-sandbox/init-env.sh --agent-env-file gen9-agent/sandbox.local.env
  fi
fi

if selected models; then
  echo "gen9-models"
  if [ -f gen9-models/.env ]; then
    kept gen9-models/.env
    bundled gen9-models/.env postgres LITELLM_DB_HOST
    # Settings added since: the admin API's database role and port, generated once
    if ! grep -Eq '^GEN9_ADMIN_DB_PASSWORD=.+' gen9-models/.env; then
      set_env gen9-models/.env GEN9_ADMIN_DB_PASSWORD "$(openssl rand -hex 16)"
      grep -q '^GEN9_MODELS_ADMIN_PORT=' gen9-models/.env || set_env gen9-models/.env GEN9_MODELS_ADMIN_PORT 19001
      echo "  added the admin API's settings to gen9-models/.env"
    fi
    if ! grep -Eq '^GEN9_SEARXNG_SECRET=.+' gen9-models/.env; then
      set_env gen9-models/.env GEN9_SEARXNG_SECRET "$(openssl rand -hex 32)"
      echo "  added SearXNG's secret to gen9-models/.env"
    fi
    if ! grep -Eq '^GEN9_AGENT_API_MODELS_KEY=.+' gen9-models/.env; then
      set_env gen9-models/.env GEN9_AGENT_API_MODELS_KEY "sk-gen9-agent-api-$(openssl rand -hex 24)"
      echo "  added the key of gen9-agent's API (it may only embed) to gen9-models/.env"
    fi
    if ! grep -Eq '^GEN9_EVALS_MODELS_KEY=.+' gen9-models/.env; then
      set_env gen9-models/.env GEN9_EVALS_MODELS_KEY "sk-gen9-evals-$(openssl rand -hex 24)"
      grep -q '^GEN9_EVALS_BUDGET_USD=' gen9-models/.env || set_env gen9-models/.env GEN9_EVALS_BUDGET_USD 5
      echo "  added the key of gen9-agent's evals (it may only call chat, \$5 a day) to gen9-models/.env"
    fi
    # The daily backstop on gen9-agent's key: shown in .env so it can be changed
    if ! grep -q '^GEN9_AGENT_BUDGET_USD=' gen9-models/.env; then
      set_env gen9-models/.env GEN9_AGENT_BUDGET_USD 5
      echo "  added gen9-agent's daily budget (\$5, GEN9_AGENT_BUDGET_USD) to gen9-models/.env"
    fi
    [ -f gen9-agent/models.local.env ] ||
      gen9-models/init-env.sh --from-env --agent-env-file gen9-agent/models.local.env
    [ -f gen9-agent/models-api.local.env ] ||
      gen9-models/init-env.sh --from-env --agent-api-env-file gen9-agent/models-api.local.env
    [ -f gen9-agent/models-evals.local.env ] ||
      gen9-models/init-env.sh --from-env --evals-env-file gen9-agent/models-evals.local.env
  else
    # The provider key: gen9-agent's, when it already has one; otherwise asked for (hidden)
    from=()
    [ -f gen9-agent/.env ] && ! no_openai_key && from=(--openai-key-from gen9-agent/.env)
    gen9-models/init-env.sh ${from[@]+"${from[@]}"} --keys-later --agent-env-file gen9-agent/models.local.env \
      --agent-api-env-file gen9-agent/models-api.local.env \
      --evals-env-file gen9-agent/models-evals.local.env
    if [ ${#from[@]} -gt 0 ]; then
      echo "  OPENAI_API_KEY copied from gen9-agent/.env"
    else
      provider_key OPENAI_API_KEY "speak, transcribe and image"
    fi
    provider_key OPENROUTER_API_KEY "chat, vision and embed"
  fi
fi

if selected agent; then
  echo "gen9-agent"
  if [ -f gen9-agent/.env ]; then
    kept gen9-agent/.env
    case $(ls -l gen9-agent/.env) in
      -rw-------*) ;;
      *) chmod 600 gen9-agent/.env && echo "  gen9-agent/.env: now readable only by you, as it holds your API keys" ;;
    esac
  else
    (umask 077 && cp gen9-agent/sample.env gen9-agent/.env)
    echo "  created gen9-agent/.env from sample.env"
  fi
  # The key that encrypts what gen9-agent sends to Temporal (codec.py): generated once, kept
  if ! grep -Eq '^GEN9_SECRET_KEYS=.+' gen9-agent/.env; then
    set_env gen9-agent/.env GEN9_SECRET_KEYS "k$(date -u +%Y%m%d):$(openssl rand -base64 32)"
    echo "  generated GEN9_SECRET_KEYS (secrets people give Gen9, such as connector tokens) in gen9-agent/.env"
  fi
  # The private addresses this local Gen9's connectors may reach: e2e's test MCP servers (sign-in,
  # e2e/connectors-oauth.mjs; asking the person, e2e/elicitation.mjs; an app, e2e/apps.mjs; one that
  # trusts Keycloak, e2e/connectors-keycloak.mjs, and that Keycloak's test realms). Leave them out
  # where others' connectors run: set it to [] (or hosts of your own), which this keeps. It's added
  # when missing, and an older copy of e2e's own list (before 17804) is brought up to date
  if ! grep -q '^CONNECTORS_ALLOWED_HOSTS=' gen9-agent/.env ||
    { grep -q '^CONNECTORS_ALLOWED_HOSTS=.*host\.docker\.internal:1780[0-9]' gen9-agent/.env &&
      ! grep -q '^CONNECTORS_ALLOWED_HOSTS=.*17804' gen9-agent/.env; }; then
    set_env gen9-agent/.env CONNECTORS_ALLOWED_HOSTS '["host.docker.internal:17801", "host.docker.internal:17802", "host.docker.internal:17803", "host.docker.internal:17804", "host.docker.internal:15000"]'
    echo "  allowed connectors to reach e2e's test server (CONNECTORS_ALLOWED_HOSTS) in gen9-agent/.env"
  fi
  # Where the worker sends notification emails in development: the Keycloak stack's Mailpit, on the
  # gen9-keycloak network. Set SMTP_URL to a real server where people should get them
  if ! grep -Eq '^SMTP_URL=' gen9-agent/.env; then
    set_env gen9-agent/.env SMTP_URL 'smtp://gen9-mailpit:1025'
    set_env gen9-agent/.env SMTP_FROM 'Gen9 <gen9@gen9.local>'
    echo "  set SMTP_URL and SMTP_FROM (notification emails, to Mailpit) in gen9-agent/.env"
  fi
  # The git server e2e's plugins check serves marketplaces from (e2e/plugins.mjs), which plugin
  # sources may reach although private, over http. Leave it out where others add sources: set it
  # to [] (or hosts of your own), which this keeps
  if ! grep -q '^PLUGIN_SOURCES_ALLOWED_HOSTS=' gen9-agent/.env; then
    set_env gen9-agent/.env PLUGIN_SOURCES_ALLOWED_HOSTS '["host.docker.internal:17805"]'
    echo "  allowed plugin sources to reach e2e's git server (PLUGIN_SOURCES_ALLOWED_HOSTS) in gen9-agent/.env"
  fi
  if ! grep -Eq '^TEMPORAL_PAYLOAD_KEYS=.+' gen9-agent/.env; then
    set_env gen9-agent/.env TEMPORAL_PAYLOAD_KEYS "k$(date -u +%Y%m%d):$(openssl rand -base64 32)"
    echo "  generated TEMPORAL_PAYLOAD_KEYS (Temporal payload encryption) in gen9-agent/.env"
  fi
  # Provider keys live in gen9-models since the router: an older gen9-agent/.env gives its key up
  if grep -q '^OPENAI_API_KEY=' gen9-agent/.env; then
    if [ -f gen9-models/.env ] && ! no_openai_key && ! grep -Eq '^OPENAI_API_KEY=.+' gen9-models/.env; then
      set_env gen9-models/.env OPENAI_API_KEY "$(sed -n 's/^OPENAI_API_KEY=//p' gen9-agent/.env | tail -n 1)"
      echo "  copied OPENAI_API_KEY to gen9-models/.env"
    fi
    if [ -f gen9-models/.env ] && grep -Eq '^OPENAI_API_KEY=.+' gen9-models/.env; then
      unset_env gen9-agent/.env OPENAI_API_KEY
      echo "  removed OPENAI_API_KEY from gen9-agent/.env: models are reached through gen9-models"
    fi
  fi
fi

if selected ui; then
  echo "gen9-ui"
  if [ -f gen9-ui/.env ]; then kept gen9-ui/.env; bundled gen9-ui/.env valkey SESSION_STORE_URL; else gen9-ui/init-env.sh; fi
fi

if selected edge && [ -z "${DOMAIN:-}" ]; then
  echo "gen9-edge"
  if [ -f gen9-edge/.env ]; then
    kept gen9-edge/.env
  else
    echo "  optional, left out: make setup DOMAIN=<domain> serves Gen9 under it, over TLS"
  fi
fi

# A value of FILE's KEY (a port: never a secret), or DEFAULT
value_of() { local v; v=$(sed -n "s/^$2=//p" "$1" 2>/dev/null | tail -n 1); echo "${v:-$3}"; }
# KEY=VALUE in FILE, if FILE exists (a stack not set up yet gets it from its own setup later)
set_if_there() { if [ -f "$1" ]; then set_env "$1" "$2" "$3"; fi; }

# Every address browsers and terminals reach Gen9 by, in each stack's settings files: under the
# domain, one host per service, as gen9-edge serves them; for localhost, each stack's own port.
# Containers call each other inside and keep their addresses
public_addresses() {
  local ui id api traces media temporal apps
  if [ "$1" = localhost ]; then
    ui="http://localhost:$(value_of gen9-ui/.env GEN9_UI_PORT 14000)"
    id="http://localhost:$(value_of gen9-keycloak/.env KEYCLOAK_PORT 15000)"
    api="http://localhost:$(value_of gen9-agent/.env GEN9_AGENT_PORT 17000)"
    traces="http://localhost:$(value_of gen9-langfuse/.env LANGFUSE_PORT 13000)"
    media="http://localhost:$(value_of gen9-langfuse/.env LANGFUSE_MEDIA_PORT 13001)"
    temporal="http://localhost:$(value_of gen9-temporal/.env GEN9_TEMPORAL_UI_PORT 18000)"
    apps="http://{id}.apps.localhost:$(value_of gen9-ui/.env GEN9_UI_SANDBOX_PORT 14003)"
  else
    ui="https://$1" id="https://id.$1" api="https://api.$1" traces="https://traces.$1"
    media="https://traces-media.$1" temporal="https://temporal.$1" apps="https://{id}.apps.$1"
  fi
  set_if_there gen9-keycloak/.env KC_HOSTNAME "$id"
  set_if_there gen9-keycloak/.env GEN9_UI_URL "$ui"
  set_if_there gen9-keycloak/.env GEN9_TEMPORAL_UI_URL "$temporal"
  set_if_there gen9-keycloak/.env GEN9_MCP_URL "$api/mcp"
  set_if_there gen9-keycloak/.env GEN9_A2A_URL "$api/a2a"
  set_if_there gen9-ui/keycloak.local.env KEYCLOAK_ISSUER "$id/realms/gen9"
  set_if_there gen9-agent/keycloak.local.env KEYCLOAK_ISSUER "$id/realms/gen9"
  set_if_there gen9-temporal/keycloak.local.env TEMPORAL_AUTH_ISSUER_URL "$id/realms/gen9"
  set_if_there gen9-ui/.env GEN9_UI_URL "$ui"
  set_if_there gen9-ui/.env MCP_APPS_SANDBOX_URL "$apps"
  set_if_there gen9-agent/.env GEN9_UI_URL "$ui"
  set_if_there gen9-agent/.env GEN9_API_PUBLIC_URL "$api"
  set_if_there gen9-agent/.env TEMPORAL_UI_URL "$temporal"
  set_if_there gen9-langfuse/.env NEXTAUTH_URL "$traces"
  # The media store's address only when it is the bundled MinIO (COMPOSE_PROFILES lists it, or an
  # .env from before the stores were profiles has none): an S3 elsewhere keeps its own
  if [ -f gen9-langfuse/.env ] && { ! grep -q '^COMPOSE_PROFILES=' gen9-langfuse/.env ||
    case ",$(sed -n 's/^COMPOSE_PROFILES=//p' gen9-langfuse/.env | tail -n 1)," in *,minio,*) true ;; *) false ;; esac; }; then
    set_env gen9-langfuse/.env LANGFUSE_MEDIA_PUBLIC_URL "$media"
    set_env gen9-langfuse/.env LANGFUSE_S3_BATCH_EXPORT_EXTERNAL_ENDPOINT "$media"
  fi
  set_if_there gen9-temporal/.env GEN9_TEMPORAL_UI_URL "$temporal"
  set_if_there gen9-temporal/.env GEN9_TEMPORAL_CODEC_URL "$api/v1/temporal/codec"
}

if [ -n "${DOMAIN:-}" ]; then
  echo "gen9-edge"
  if [ "$DOMAIN" = localhost ]; then
    public_addresses localhost
    rm -f gen9-edge/.env gen9-keycloak/edge.local.env
    echo "  every address back on this machine's ports; gen9-edge no longer set up (make down STACKS=edge stops it)"
  else
    [ -f gen9-edge/.env ] || (umask 077 && : >gen9-edge/.env)
    set_env gen9-edge/.env GEN9_DOMAIN "$DOMAIN"
    [ -z "${EDGE_TLS:-}" ] || set_env gen9-edge/.env GEN9_EDGE_TLS "$EDGE_TLS"
    # A wildcard of one's own for the apps' hosts, given before they had certificates on demand
    if [ "$(value_of gen9-edge/.env GEN9_EDGE_APPS_TLS internal)" != internal ] && ! grep -q '^GEN9_EDGE_APPS=' gen9-edge/.env; then
      set_env gen9-edge/.env GEN9_EDGE_APPS own
      echo "  gen9-edge/.env: GEN9_EDGE_APPS=own, so the apps' hosts keep the wildcard in GEN9_EDGE_APPS_TLS"
    fi
    # Keycloak behind the edge reads its forwarded headers (gen9-keycloak/compose.yaml)
    (umask 077 && printf 'KC_PROXY_HEADERS=xforwarded\n' >gen9-keycloak/edge.local.env)
    public_addresses "$DOMAIN"
    echo "  Gen9 under $DOMAIN: every address in the stacks' settings files, one host per service (gen9-edge/README.md)"
  fi
fi

# Where gen9-agent reaches its database: gen9-postgres/.env's server of one's own, if it names one
# (docs/operations.md, "External services"), copied into gen9-agent/.env, so both stacks agree
if { selected postgres || selected agent; } && [ -f gen9-postgres/.env ] && [ -f gen9-agent/.env ]; then
  for key in GEN9_POSTGRES_SERVER GEN9_POSTGRES_SERVER_PORT GEN9_POSTGRES_SSLMODE GEN9_POSTGRES_SSLROOTCERT; do
    value=$(sed -n "s/^$key=//p" gen9-postgres/.env | tail -n 1)
    if [ -n "$value" ]; then
      [ "$(sed -n "s/^$key=//p" gen9-agent/.env | tail -n 1)" = "$value" ] ||
        { set_env gen9-agent/.env "$key" "$value"; echo "  gen9-agent/.env: $key as in gen9-postgres/.env"; }
    elif grep -q "^$key=" gen9-agent/.env; then
      unset_env gen9-agent/.env "$key"; echo "  gen9-agent/.env: no $key, as in gen9-postgres/.env"
    fi
  done
fi

# Settings files one stack writes for another: without them `make up` stops at that stack
missing=()
if selected agent; then
  [ -f gen9-agent/postgres.local.env ] || missing+=("gen9-agent/postgres.local.env (make setup STACKS=postgres)")
  [ -f gen9-agent/keycloak.local.env ] || missing+=("gen9-agent/keycloak.local.env (make setup STACKS=keycloak)")
  [ -f gen9-agent/models.local.env ] || missing+=("gen9-agent/models.local.env (make setup STACKS=models)")
  [ -f gen9-agent/sandbox.local.env ] || missing+=("gen9-agent/sandbox.local.env (make setup STACKS=sandbox)")
fi
if selected ui; then
  [ -f gen9-ui/keycloak.local.env ] || missing+=("gen9-ui/keycloak.local.env (make setup STACKS=keycloak)")
fi
if selected temporal; then
  [ -f gen9-temporal/keycloak.local.env ] || missing+=("gen9-temporal/keycloak.local.env (make setup STACKS=keycloak)")
fi
echo
if [ ${#missing[@]} -gt 0 ]; then
  echo "Still missing, written by another stack's setup:"
  printf '  %s\n' "${missing[@]}"
  exit 1
fi
if [ "$ARGS" = " ${ALL[*]} " ]; then echo "Ready. Next: make up"; else echo "Ready. Next: make up STACKS=\"$*\""; fi
# Who signs in where, once make up lists the addresses (the passwords stay in the files)
setting() { sed -n "s/^$2=//p" "$1" | tail -n 1; }
if [ -f gen9-keycloak/.env ] || [ -f gen9-langfuse/.env ]; then
  echo "To sign in:"
  if [ -f gen9-keycloak/.env ]; then
    echo "  Gen9:               $(setting gen9-keycloak/.env GEN9_SEED_ADMIN_EMAIL) (admin; code: make admin-code) or $(setting gen9-keycloak/.env GEN9_SEED_USER_EMAIL), passwords: grep ^GEN9_SEED_ gen9-keycloak/.env"
    echo "  Keycloak's console: admin, password: grep ^KC_BOOTSTRAP_ADMIN_PASSWORD= gen9-keycloak/.env"
  fi
  if [ -f gen9-langfuse/.env ]; then
    echo "  Langfuse:           $(setting gen9-langfuse/.env LANGFUSE_INIT_USER_EMAIL), password: grep ^LANGFUSE_INIT_USER_PASSWORD= gen9-langfuse/.env"
  fi
fi
