#!/usr/bin/env bash
# Generate the .env for gen9-models: its Postgres password, LiteLLM's master and salt keys,
# gen9-agent's virtual keys (its worker's, its API's that may only embed and rerank, and its evals'
# that may only call chat), the host port, and the provider keys config.yaml uses.
# Portable: bash 3.2+ (macOS and Linux). Run with --help.
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
OUTPUT="$SCRIPT_DIR/.env" PORT="19000" ADMIN_PORT="19001" AGENT_ENV_FILE="" API_ENV_FILE="" EVALS_ENV_FILE="" OPENAI_FROM="" FORCE="false" FROM_ENV="false" KEYS_LATER="false"

usage() {
  cat <<'USAGE'
Generate .env for the gen9-models stack (the model router).

Usage: ./init-env.sh [--port N] [--openai-key-from FILE] [--agent-env-file FILE]
                     [--agent-api-env-file FILE] [--evals-env-file FILE] [-o FILE|-] [--force]
       ./init-env.sh --from-env [--agent-env-file FILE] [--agent-api-env-file FILE]
                     [--evals-env-file FILE] [--force]

  --port N                Host port for the router's API, bound to 127.0.0.1         [19000]
  --openai-key-from FILE  Copy OPENAI_API_KEY from FILE (for example ../gen9-agent/.env); without
                          it, OPENAI_API_KEY is left empty for you to fill in
  --agent-env-file FILE   Also write gen9-agent's settings (router URL and its worker's virtual
                          key), e.g. ../gen9-agent/models.local.env
  --agent-api-env-file FILE
                          Also write the settings of gen9-agent's API (router URL and a key that
                          may only embed and rerank), e.g. ../gen9-agent/models-api.local.env
  --evals-env-file FILE   Also write the settings of gen9-agent's evals (router URL and a key that
                          may only call chat, with a daily budget: the judge's),
                          e.g. ../gen9-agent/models-evals.local.env
  -o, --output FILE       File to write, or - for stdout                  [.env next to this script]
  --force                 Overwrite existing output files
  --from-env              Only write the gen9-agent files named, from the existing .env (a lost
                          settings file)
  --keys-later            Don't say to fill in the provider keys: the caller sets them next
                          (make setup, from the environment or asked for)

Other providers: add their keys to .env (ANTHROPIC_API_KEY, GEMINI_API_KEY, …) and their models to
config.yaml. gen9-agent's containers reach the router at gen9-models:4000.
USAGE
}

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
rand_hex() { if command -v openssl >/dev/null 2>&1; then openssl rand -hex "$1"; else od -An -vtx1 -N "$1" /dev/urandom | tr -d ' \n'; echo; fi; }
env_value() { sed -n "s/^$1=//p" "$2" | tail -n 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --*=*) flag=${1%%=*} value=${1#*=}; shift; set -- "$flag" "$value" "$@"; continue ;;
  esac
  case "$1" in
    --port) [ $# -ge 2 ] || die "--port needs a value"; PORT=$2; shift 2 ;;
    --openai-key-from) [ $# -ge 2 ] || die "$1 needs a value"; OPENAI_FROM=$2; shift 2 ;;
    --agent-env-file) [ $# -ge 2 ] || die "$1 needs a value"; AGENT_ENV_FILE=$2; shift 2 ;;
    --agent-api-env-file) [ $# -ge 2 ] || die "$1 needs a value"; API_ENV_FILE=$2; shift 2 ;;
    --evals-env-file) [ $# -ge 2 ] || die "$1 needs a value"; EVALS_ENV_FILE=$2; shift 2 ;;
    -o | --output) [ $# -ge 2 ] || die "$1 needs a value"; OUTPUT=$2; shift 2 ;;
    --force) FORCE="true"; shift ;;
    --from-env) FROM_ENV="true"; shift ;;
    --keys-later) KEYS_LATER="true"; shift ;;
    -h | --help) usage; exit 0 ;;
    *) die "unknown option: $1 (see --help)" ;;
  esac
done

write_private() { # $1=path; content on stdin. Written atomically with mode 600.
  local tmp
  tmp=$(umask 077 && mktemp "$(dirname "$1")/.init-env.XXXXXX")
  cat >"$tmp" && chmod 600 "$tmp" && mv -f "$tmp" "$1"
  printf 'wrote %s\n' "$1"
}

write_agent_file() {
  [ -n "$AGENT_ENV_FILE" ] || return 0
  # gen9-agent on the host uses this URL; its compose.yaml points containers at gen9-models:4000
  printf '# gen9-models settings for gen9-agent (generated %s)\nGEN9_MODELS_URL=http://localhost:%s\nGEN9_MODELS_ADMIN_URL=http://localhost:%s\nGEN9_MODELS_KEY=%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$PORT" "$ADMIN_PORT" "$AGENT_KEY" | write_private "$AGENT_ENV_FILE"
}

write_api_file() {
  [ -n "$API_ENV_FILE" ] || return 0
  # Only gen9-agent's API container loads it (gen9-agent/compose.yaml): its key may only embed and
  # rerank
  printf '# gen9-models settings for gen9-agent'"'"'s API: a key that may only embed and rerank (generated %s)\nGEN9_MODELS_URL=http://localhost:%s\nGEN9_MODELS_KEY=%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$PORT" "$API_KEY" | write_private "$API_ENV_FILE"
}

write_evals_file() {
  [ -n "$EVALS_ENV_FILE" ] || return 0
  # make evals loads it on the host: its key may only call chat (the judge), within a daily budget
  printf '# gen9-models settings for gen9-agent'"'"'s evals: a key that may only call chat (generated %s)\nGEN9_MODELS_URL=http://localhost:%s\nGEN9_MODELS_KEY=%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$PORT" "$EVALS_KEY" | write_private "$EVALS_ENV_FILE"
}

if [ "$FROM_ENV" = "true" ]; then
  [ -f "$OUTPUT" ] || die "$OUTPUT doesn't exist: run without --from-env to create it"
  [ -n "$AGENT_ENV_FILE$API_ENV_FILE$EVALS_ENV_FILE" ] || die "--from-env writes --agent-env-file, --agent-api-env-file or --evals-env-file; name one"
  PORT=$(env_value GEN9_MODELS_PORT "$OUTPUT") AGENT_KEY=$(env_value GEN9_AGENT_MODELS_KEY "$OUTPUT")
  API_KEY=$(env_value GEN9_AGENT_API_MODELS_KEY "$OUTPUT") EVALS_KEY=$(env_value GEN9_EVALS_MODELS_KEY "$OUTPUT")
  ADMIN_PORT=$(env_value GEN9_MODELS_ADMIN_PORT "$OUTPUT"); ADMIN_PORT=${ADMIN_PORT:-19001}
  [ -n "$PORT" ] && [ -n "$AGENT_KEY" ] || die "$OUTPUT has no GEN9_MODELS_PORT or GEN9_AGENT_MODELS_KEY"
  [ -z "$API_ENV_FILE" ] || [ -n "$API_KEY" ] || die "$OUTPUT has no GEN9_AGENT_API_MODELS_KEY: run make setup STACKS=models"
  [ -z "$EVALS_ENV_FILE" ] || [ -n "$EVALS_KEY" ] || die "$OUTPUT has no GEN9_EVALS_MODELS_KEY: run make setup STACKS=models"
  for f in "$AGENT_ENV_FILE" "$API_ENV_FILE" "$EVALS_ENV_FILE"; do
    if [ -n "$f" ] && [ -e "$f" ] && [ "$FORCE" != "true" ]; then die "$f already exists; pass --force to overwrite"; fi
  done
  write_agent_file
  write_api_file
  write_evals_file
  exit 0
fi

[[ "$PORT" =~ ^[0-9]+$ ]] && [ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ] || die "--port must be 1-65535"
for f in "$OUTPUT" "$AGENT_ENV_FILE" "$API_ENV_FILE" "$EVALS_ENV_FILE"; do
  if [ -n "$f" ] && [ "$f" != "-" ] && [ -e "$f" ] && [ "$FORCE" != "true" ]; then die "$f already exists; pass --force to overwrite"; fi
done
# The password and the salt key are fixed once the data volume exists: the salt key encrypts what
# LiteLLM stores, and a new password would lock it out of its database
if [ "$OUTPUT" = "$SCRIPT_DIR/.env" ] && command -v docker >/dev/null &&
  docker volume inspect gen9-models_postgres_data >/dev/null 2>&1; then
  die "volume gen9-models_postgres_data exists; new secrets would not match it.
  start fresh (DELETES KEYS AND SPEND): docker compose down -v"
fi
OPENAI_API_KEY=""
if [ -n "$OPENAI_FROM" ]; then
  [ -f "$OPENAI_FROM" ] || die "--openai-key-from: $OPENAI_FROM doesn't exist"
  OPENAI_API_KEY=$(env_value OPENAI_API_KEY "$OPENAI_FROM")
fi

AGENT_KEY="sk-gen9-agent-$(rand_hex 24)" API_KEY="sk-gen9-agent-api-$(rand_hex 24)"
EVALS_KEY="sk-gen9-evals-$(rand_hex 24)"
render() {
  cat <<ENV
# Generated by init-env.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ). Do not commit.
COMPOSE_PROJECT_NAME=gen9-models
# The bundled services this stack runs: postgres (without it, LITELLM_DB_HOST names another server:
# docs/operations.md, "External services"); add local for self-hosted models (README)
COMPOSE_PROFILES=postgres
GEN9_MODELS_PORT=$PORT
POSTGRES_PASSWORD=$(rand_hex 16)
# LiteLLM's admin key (key management; never given to callers) and the key that encrypts what it
# stores. Never change the salt key once the router has data.
LITELLM_MASTER_KEY=sk-$(rand_hex 24)
LITELLM_SALT_KEY=sk-$(rand_hex 24)
# gen9-agent's virtual key (scripts/ensure-keys.py creates it in the router); the admin API
# (admin/app.py) accepts it too
GEN9_AGENT_MODELS_KEY=$AGENT_KEY
# At most this many USD a day on gen9-agent's key, whoever it works for (empty: no limit): the
# backstop under each person's budget, so no loop or bug spends without end (scripts/ensure-keys.py)
GEN9_AGENT_BUDGET_USD=5
# gen9-agent's API's virtual key: the embed and rerank aliases only (search queries), no other
# model and no search tool (scripts/ensure-keys.py)
GEN9_AGENT_API_MODELS_KEY=$API_KEY
# gen9-agent's evals' virtual key (make evals): the chat alias only, for the judge, no search tool,
# within GEN9_EVALS_BUDGET_USD a day (scripts/ensure-keys.py)
GEN9_EVALS_MODELS_KEY=$EVALS_KEY
GEN9_EVALS_BUDGET_USD=5
# SearXNG's secret (the router's self-hosted web search)
GEN9_SEARXNG_SECRET=$(rand_hex 32)
# The admin API's Postgres role (gen9_admin), which may only read and delete a user's rows
GEN9_ADMIN_DB_PASSWORD=$(rand_hex 16)
GEN9_MODELS_ADMIN_PORT=$ADMIN_PORT
# Provider keys that config.yaml uses: OpenRouter for chat, vision and embed; OpenAI for speak,
# transcribe and image. Add others here (ANTHROPIC_API_KEY, GEMINI_API_KEY, …).
OPENROUTER_API_KEY=
OPENAI_API_KEY=$OPENAI_API_KEY
# Every user's default limits, applied on the next start (empty: no limit). The budget is in USD
# per period (e.g. 1d, 7d, 30d); a user over it gets a message in the chat until it resets. On by
# default, so no one person's runs (or a stolen account's) can spend without end (OWASP API4), and
# daily, a fifth of GEN9_AGENT_BUDGET_USD, so one person can't use up the day's for everyone
GEN9_USER_BUDGET_USD=1
GEN9_USER_BUDGET_PERIOD=1d
GEN9_USER_RPM=
ENV
}

if [ "$OUTPUT" = "-" ]; then render; else render | write_private "$OUTPUT"; fi
write_agent_file
write_api_file
write_evals_file
[ "$KEYS_LATER" = "true" ] ||
  printf 'note: fill in OPENROUTER_API_KEY in %s (chat, vision, embed)%s\n' "$OUTPUT" \
    "$([ -n "$OPENAI_API_KEY" ] || echo ', and OPENAI_API_KEY for speak, transcribe and image')" >&2

if [ "$OUTPUT" != "-" ]; then
  cat >&2 <<NEXT

Next:
  docker compose up -d --wait
  API: http://127.0.0.1:$PORT/v1 (OpenAI-compatible; a virtual key is needed)
NEXT
fi
