#!/usr/bin/env bash
# Generate what each stack needs before its first start: secrets, seeded users and the settings
# files apps read from each other. Used by `make setup`. Skips whatever already exists and never
# overwrites or deletes anything (start over with `make distclean`), with one exception: a provider
# key left in gen9-agent/.env from before the model router moves to gen9-models/.env.
#
#   scripts/setup.sh [STACK...]      default: postgres keycloak langfuse temporal models sandbox agent ui
#
# gen9-langfuse's first user comes from LANGFUSE_EMAIL and LANGFUSE_NAME, or is asked for. Its
# project keys go to gen9-agent/langfuse.local.env, which gen9-agent reads before its .env.
# gen9-models' provider keys come from OPENAI_API_KEY and OPENROUTER_API_KEY, or are asked for
# (hidden) when there is a terminal: `chat` needs OpenRouter's, speech and images OpenAI's.
set -euo pipefail
cd "$(dirname "$0")/.."

ALL=(postgres keycloak langfuse temporal models sandbox agent ui)
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
  else
    gen9-langfuse/init-env.sh --email "$LANGFUSE_EMAIL" --name "$LANGFUSE_NAME" \
      --agent-env-file gen9-agent/langfuse.local.env
  fi
fi

if selected temporal; then
  echo "gen9-temporal"
  if [ -f gen9-temporal/.env ]; then kept gen9-temporal/.env; else gen9-temporal/init-env.sh; fi
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
  # where others' connectors run
  if ! grep -Eq '^CONNECTORS_ALLOWED_HOSTS=.*17804' gen9-agent/.env; then
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
  # sources may reach although private, over http. Leave it out where others add sources
  if ! grep -Eq '^PLUGIN_SOURCES_ALLOWED_HOSTS=.*17805' gen9-agent/.env; then
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
  if [ -f gen9-ui/.env ]; then kept gen9-ui/.env; else gen9-ui/init-env.sh; fi
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
