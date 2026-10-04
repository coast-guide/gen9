#!/usr/bin/env bash
# Delete Gen9 stacks for good: containers, data volumes and networks, and with --secrets also their
# .env and settings files, back to a fresh clone. Used by `make wipe` and `make distclean` (all
# stacks, or STACKS).
#
#   scripts/wipe.sh [--secrets] [--yes] STACK...      STACK: postgres keycloak langfuse temporal models sandbox agent ui edge
#
# Lists what it will delete and asks you to type "yes" (like `terraform destroy`); --yes skips the
# question, and without a terminal it refuses instead of waiting. Resources are found by the label
# Docker Compose puts on them (com.docker.compose.project=gen9-STACK), so this also works when a
# stack's .env is already gone and `docker compose down` would refuse to read the compose file.
# Images are kept, and so are the local profile's downloaded models (named, with the command that
# removes them). With --secrets the settings files go too, provider keys in gen9-models/.env
# included: make setup creates them again and asks for your OpenAI key.
set -euo pipefail
cd "$(dirname "$0")/.."

SECRETS=false YES=false STACKS=()
for arg in "$@"; do
  case $arg in
    --secrets) SECRETS=true ;;
    --yes) YES=true ;;
    -*) echo "unknown option: $arg" >&2; exit 2 ;;
    *) STACKS+=("$arg") ;;
  esac
done
[ ${#STACKS[@]} -gt 0 ] || { echo "usage: scripts/wipe.sh [--secrets] [--yes] STACK..." >&2; exit 2; }
# Never another copy of Gen9's containers and data (scripts/elsewhere.sh)
scripts/elsewhere.sh --refuse "$($SECRETS && echo distclean || echo wipe)" "${STACKS[@]}" || exit 1

# What each stack's volumes hold, in plain words
holds() {
  case $1 in
    postgres) echo "app database: Gen9 users, chats, agent memory" ;;
    keycloak) echo "accounts, passwords, passkeys, sessions, sign-in history; Mailpit's emails" ;;
    ui) echo "web sessions: everyone gets signed out" ;;
    agent) echo "no data of its own" ;;
    langfuse) echo "traces, Langfuse users, projects and API keys" ;;
    temporal) echo "workflow state: running and waiting runs, schedules, workflow history" ;;
    models) echo "the router's virtual keys, budgets and spend history" ;;
    sandbox) echo "every chat's environment (its containers and the files in them) and the server's records of them" ;;
    edge) echo "its certificates and its own CA: browsers that trusted the CA need the new one's root" ;;
    *) echo "unknown stack: $1 (postgres keycloak langfuse temporal models sandbox agent ui edge)" >&2; exit 2 ;;
  esac
}

# Each stack's secrets and settings files: what its init-env.sh generates (gen9-agent: what make
# setup made from sample.env; gen9-models: with the provider keys you gave it)
generated() {
  case $1 in
    postgres) echo gen9-postgres/.env gen9-agent/postgres.local.env gen9-agent/postgres-app.local.env ;;
    keycloak) echo gen9-keycloak/.env gen9-ui/keycloak.local.env gen9-agent/keycloak.local.env gen9-temporal/keycloak.local.env ;;
    ui) echo gen9-ui/.env ;;
    agent) echo gen9-agent/.env ;;
    langfuse) echo gen9-langfuse/.env gen9-agent/langfuse.local.env ;;
    sandbox) echo gen9-sandbox/.env gen9-agent/sandbox.local.env ;;
    temporal) echo gen9-temporal/.env gen9-temporal/tls.local.env ;;
    models) echo gen9-models/.env gen9-agent/models.local.env gen9-agent/models-api.local.env gen9-agent/models-evals.local.env ;;
    edge) echo gen9-edge/.env gen9-keycloak/edge.local.env ;;
  esac
}

# "1 container", "2 containers"
count() { [ "$1" -eq 1 ] && echo "1 $2" || echo "$1 ${2}s"; }

# A stack's own containers, as Compose counts them, have a oneoff label too: OpenSandbox's egress
# sidecars carry the project label of the image Compose built for them, but not that one
OWN=label=com.docker.compose.oneoff

# Every container this deletes, by name: a stack's shared network goes only if nothing else uses it
deleting=" "
for stack in "${STACKS[@]}"; do
  deleting+="$(docker ps -a --filter "label=com.docker.compose.project=gen9-$stack" --filter "$OWN" --format '{{.Names}}' | tr '\n' ' ')"
done

containers=() volumes=() networks=() files=() dependents=() downloads=()
echo "This deletes, for good:"
for stack in "${STACKS[@]}"; do
  what=$(holds "$stack")
  label="label=com.docker.compose.project=gen9-$stack"
  c=0 v=0 n=0
  for id in $(docker ps -aq --filter "$label" --filter "$OWN"); do containers+=("$id"); c=$((c + 1)); done
  # The environments OpenSandbox started through the Docker socket: not part of the Compose project
  if [ "$stack" = sandbox ]; then
    for id in $(docker ps -aq --filter label=opensandbox.io/id) $(docker ps -aq --filter label=opensandbox.io/egress-sidecar-for); do
      containers+=("$id"); c=$((c + 1))
    done
  fi
  for id in $(docker network ls -q --filter "$label"); do networks+=("$id"); n=$((n + 1)); done
  # gen9-<stack>, the network other stacks reach this one on (make up creates it)
  in_use=""
  if docker network inspect "gen9-$stack" >/dev/null 2>&1; then
    for user in $(docker network inspect "gen9-$stack" -f '{{range .Containers}}{{.Name}} {{end}}'); do
      [[ $deleting == *" $user "* ]] && continue
      in_use="$in_use $user"
      # The stack it belongs to: it keeps running, on data this deletes
      user_stack=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$user")
      user_stack=${user_stack#gen9-}
      [[ " ${dependents[*]:-} " == *" $user_stack "* ]] || dependents+=("$user_stack")
    done
    if [ -z "$in_use" ]; then networks+=("gen9-$stack"); n=$((n + 1)); fi
  fi
  stack_volumes=()
  for name in $(docker volume ls -q --filter "$label"); do
    # Downloaded model weights (gen9-models' local profile): downloads, like the images, not anyone's
    # data, and gigabytes to fetch again. Kept, and named below with the command that removes them
    case $name in
      gen9-models_ollama_models | gen9-models_reranker_models) downloads+=("$name"); continue ;;
    esac
    volumes+=("$name"); stack_volumes+=("$name")
  done
  v=${#stack_volumes[@]}
  if [ $((c + v + n)) -gt 0 ]; then
    echo "  gen9-$stack ($what)"
    [ "$v" -eq 0 ] || printf '      volume %s\n' "${stack_volumes[@]}"
    rest=""
    [ "$c" -eq 0 ] || rest="$(count "$c" container) (stopped first)"
    [ "$n" -eq 0 ] || rest="${rest:+$rest, }$(count "$n" network)"
    [ -z "$rest" ] || echo "      $rest"
    [ -z "$in_use" ] || echo "      keeps network gen9-$stack: still used by$in_use"
  fi
  if $SECRETS; then
    for file in $(generated "$stack"); do [ -e "$file" ] && files+=("$file"); done
  fi
done
if [ ${#files[@]} -gt 0 ]; then
  notes=""
  [[ " ${STACKS[*]} " != *" keycloak "* ]] || notes="$notes; seeded users get new passwords"
  [[ " ${files[*]} " != *" gen9-models/.env "* ]] || notes="$notes; it asks for your OpenAI key again"
  echo "  secrets and settings files (make setup makes new ones$notes):"
  printf '      %s\n' "${files[@]}"
fi
if [ $((${#containers[@]} + ${#volumes[@]} + ${#networks[@]} + ${#files[@]})) -eq 0 ]; then
  echo "  nothing: already clean."
  exit 0
fi
others=""
for stack in postgres keycloak langfuse temporal models sandbox agent ui edge; do
  [[ " ${STACKS[*]} " == *" $stack "* ]] || others="$others gen9-$stack"
done
if $SECRETS; then
  echo "Kept: images."
else
  echo "Kept: images, and the stacks' .env and settings files (make distclean deletes those too)."
fi
if [ ${#downloads[@]} -gt 0 ]; then
  echo "Kept too: the downloaded models (${downloads[*]}). To remove them: docker volume rm ${downloads[*]}"
fi
[ -z "$others" ] || echo "Not touched:$others"

if ! $YES; then
  if [ ! -t 0 ]; then
    echo "No terminal to confirm in: rerun with YES=1 to delete without asking." >&2
    exit 1
  fi
  printf 'Type "yes" to delete this: '
  read -r answer || { answer=""; echo; }
  [ "$answer" = yes ] || { echo "Nothing deleted."; exit 1; }
fi

# -v: with their anonymous volumes, which `docker rm` otherwise leaves behind
[ ${#containers[@]} -eq 0 ] || docker rm -f -v "${containers[@]}" >/dev/null
[ ${#volumes[@]} -eq 0 ] || docker volume rm "${volumes[@]}" >/dev/null
[ ${#networks[@]} -eq 0 ] || docker network rm "${networks[@]}" >/dev/null
[ ${#files[@]} -eq 0 ] || rm -f "${files[@]}"
echo "Deleted."
if [ ${#dependents[@]} -gt 0 ]; then
  # e.g. gen9-agent keeps its connection settings but needs its migrations again on a new database
  echo "Still running on what this deleted:$(printf ' gen9-%s' "${dependents[@]}"). Start them again with it:"
  echo "  make up STACKS=\"${STACKS[*]} ${dependents[*]}\""
fi
