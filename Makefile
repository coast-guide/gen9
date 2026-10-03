# Bring up Gen9's Docker Compose stacks together, while keeping them decoupled.
#
# Each stack runs from its own folder (`cd gen9-<name> && docker compose ...`), so it keeps its own
# project name, .env, volumes and network: exactly as if you ran `docker compose` in that folder.
# Stacks are not merged with Compose `include`, which would rename volumes to one shared project.
#
# Every command covers all stacks unless you pass STACKS, e.g. `make up STACKS="keycloak ui"`
# (names from `make stacks`; `gen9-ui` works too). Stacks start in the order of ALL_STACKS and
# stop in reverse.
#
# Add a stack: create gen9-<name>/ with a Compose file and a README, add <name> to ALL_STACKS
# after the stacks it needs, describe it in DESC_<name> and list the files it needs in NEEDS_<name>.
#
# Works with GNU Make 3.81 (macOS default) and later.

ALL_STACKS := postgres keycloak langfuse temporal models sandbox agent ui edge
TAIL       ?= 50

DESC_postgres := Postgres 18 + pgvector + pg_textsearch (BM25): the app database  [16000]
DESC_keycloak := Keycloak: accounts and sign-in, own Postgres + Mailpit  [15000-15003]
DESC_langfuse := Langfuse: traces of agent runs  [13000-13007]
DESC_temporal := Temporal: durable runs, approvals, schedules, own Postgres + web UI  [18000-18001]
DESC_models   := Model router: every model by alias (LiteLLM), own Postgres  [19000-19001]
DESC_sandbox  := Environments: OpenSandbox runs the code of each chat in containers of its own  [20000]
DESC_agent    := Agent API and workers: FastAPI + Deep Agents, checks Keycloak tokens  [17000]
DESC_ui       := Web app: sign-in and chat, the sandbox for connector apps, Valkey sessions  [14000-14003]
DESC_edge     := Optional: Gen9 under one domain, over TLS (Caddy), once make setup DOMAIN=… ran  [80, 443]

# Files a stack can't start without: its .env, and the settings other stacks' setup writes for it
NEEDS_postgres := .env
NEEDS_keycloak := .env
NEEDS_langfuse := .env
NEEDS_temporal := .env tls.local.env keycloak.local.env
NEEDS_models   := .env
NEEDS_sandbox  := .env
NEEDS_agent    := .env postgres.local.env postgres-app.local.env keycloak.local.env models.local.env models-api.local.env sandbox.local.env
NEEDS_ui       := .env keycloak.local.env
NEEDS_edge     := .env

# Stacks a stack calls while running. Only a note when they're down: each stack can point elsewhere.
USES_temporal := keycloak
USES_agent := postgres keycloak temporal models sandbox
USES_ui    := keycloak agent
USES_edge  := ui keycloak agent langfuse temporal

# Which stacks a command covers: STACKS (default all), in start order; gen9- prefix optional
STACKS   ?= $(ALL_STACKS)
_asked   := $(patsubst gen9-%,%,$(STACKS))
_unknown := $(filter-out $(ALL_STACKS),$(_asked))
ifneq ($(strip $(_unknown)),)
$(error Unknown stack: $(_unknown). Stacks are: $(ALL_STACKS) (see make stacks))
endif
SELECTED := $(filter $(_asked),$(ALL_STACKS))
# Stacks that run only once set up (gen9-edge: make setup DOMAIN=…): up, config and diff leave
# them out until then, with a note. RUNNING: the selected stacks that run
OPTIONAL := edge
RUNNING   = $(foreach s,$(SELECTED),$(if $(and $(filter $(s),$(OPTIONAL)),$(call missing,$(s))),,$(s)))
SKIPPED   = $(filter-out $(RUNNING),$(SELECTED))
reverse   = $(if $(1),$(call reverse,$(wordlist 2,$(words $(1)),$(1))) $(firstword $(1)))

# Needed files of stack $(1) that don't exist, and which stacks' setup writes them
# (gen9-<stack>/.env its own, <writer>[-<part>].local.env the writer's, any other *.local.env its own)
missing = $(strip $(foreach f,$(NEEDS_$(1)),$(if $(wildcard gen9-$(1)/$(f)),,gen9-$(1)/$(f))))
owner   = $(firstword $(subst /, ,$(1:gen9-%=%)))
prefix  = $(firstword $(subst -, ,$(notdir $(1:.local.env=))))
writer  = $(if $(filter $(call prefix,$(1)),$(ALL_STACKS)),$(call prefix,$(1)),$(call owner,$(1)))
writers = $(filter $(foreach f,$(1),$(if $(filter %.local.env,$(f)),$(call writer,$(f)),$(f:gen9-%/.env=%))),$(ALL_STACKS))
MISSING = $(strip $(foreach s,$(RUNNING),$(call missing,$(s))))
# stack:used pairs where the used stack isn't in STACKS (up checks whether it's running)
OUTSIDE = $(foreach s,$(RUNNING),$(foreach u,$(filter-out $(RUNNING),$(USES_$(s))),$(s):$(u)))

# down, logs and ps go by the project name Compose labels everything with, so they work whatever
# the setup state. Run from / so Compose finds no compose file in a parent folder instead.
by_name = (cd / && docker compose -p gen9-$$s $(1))
# What the logs hold reaches your terminal as text: colours dropped, any other escape sequence shown
# (^[) and other control characters as ?, never acted on: a value from outside, such as the
# username typed at a sign-in, can carry them (docs/logging.md). POSIX awk: macOS's, mawk, BusyBox
# mawk (Debian's and Ubuntu's awk) reads a pipe in blocks, so FOLLOW=1 showed nothing: -W interactive
AWK_LINES := $(if $(findstring mawk,$(shell awk -W version </dev/null 2>/dev/null)),awk -W interactive,awk)
as_text = $(AWK_LINES) '{ gsub(/\033\[[0-9;]*m/, ""); gsub(/\033/, "^["); gsub(/[\001-\010\013-\037\177]/, "?"); print; fflush() }'
# A stack's own containers, as Compose counts them: its project label and a oneoff one. Containers
# of an image Compose built carry the project label too (OpenSandbox's egress sidecars, from
# gen9-sandbox's egress image), but not oneoff.
OWN = --filter label=com.docker.compose.project=gen9-$$s --filter label=com.docker.compose.oneoff

# Settings from your shell must not leak into every stack and override its own .env
unexport COMPOSE_FILE COMPOSE_PROJECT_NAME COMPOSE_PROFILES

# Stacks reach each other over one network per stack, gen9-<stack>: that stack joins it (alias
# gen9-<stack>), and so do the stacks that call it. Compose files declare them external, so no stack
# owns another's network; up creates all of them, as a stack's Compose file needs the networks of
# the stacks it calls even when those aren't running.
NETWORKS := $(addprefix gen9-,$(filter-out $(OPTIONAL),$(ALL_STACKS)))

# Where to open what is running: each stack labels its own services (gen9.name, gen9.url)
list_urls = for s in $(SELECTED); do docker ps $(OWN) \
	  --filter label=gen9.url --format '{{.Label "gen9.name"}}|{{.Label "gen9.url"}}' | sort; done | \
	  awk -F'|' '!n++ { print ""; print "Open:" } { printf "  %-32s %s\n", $$1, $$2 }'

# wipe and distclean ask you to type "yes" first; YES=1 skips the question
WIPE_FLAGS := $(if $(filter 1,$(YES)),--yes)

.DEFAULT_GOAL := help
.PHONY: help stacks doctor up down ps logs config diff reset k8s-up k8s-diff k8s-reset k8s-down k8s-e2e k8s-stop-agents k8s-resume-agents setup admin-code backup restore stop-agents resume-agents wipe distclean fresh design-sync design-check e2e evals evals-calibrate audit sbom scan updates

help:
	@echo "Gen9: every command covers all stacks, or only STACKS=\"...\" (see make stacks)"
	@echo
	@echo "  make stacks          list the stacks"
	@echo "  make doctor          check Docker, Compose, memory, free ports and stuck deletions"
	@echo "  make setup           generate what is missing: secrets, seeded users, app settings;"
	@echo "                       asks for provider keys (or OPENROUTER_API_KEY=… OPENAI_API_KEY=…)"
	@echo "  make up              start the stacks, wait until healthy"
	@echo "                       IMAGES=<lock file or URL>: Gen9's images by digest from it, built nowhere;"
	@echo "                       IMAGES=local: built here again (the default)"
	@echo "  make down            stop them (keeps data)"
	@echo "  make stop-agents     stop every agent now: runs cancelled, scheduled tasks paused, worker stopped"
	@echo "  make resume-agents   start the worker again and unpause what stop-agents paused"
	@echo "  make ps              their containers"
	@echo "  make logs            last $(TAIL) log lines (TAIL=n; FOLLOW=1 follows, with one stack)"
	@echo "  make config          validate their Compose config"
	@echo "  make diff            what runs that differs from what's declared, changes by hand too (exit 2 if any)"
	@echo "  make reset           put back what differs: those containers recreated as declared"
	@echo "  make admin-code      the seeded admin's authenticator code now (admins need a second step)"
	@echo
	@echo "Kubernetes (kubectl's context, or K8S_CONTEXT=…; settings: deploy/values.yaml, or K8S_VALUES=file):"
	@echo "  make k8s-up IMAGES=<lock>  each stack a Helm release in its namespace gen9-<stack>, images by digest"
	@echo "  make k8s-diff        what differs from what's declared, changes by hand too (exit 2 if any)"
	@echo "  make k8s-reset       put back what was changed by hand: each object replaced with what's declared"
	@echo "  make k8s-down        uninstall them (keeps volumes and Secrets)"
	@echo "  make k8s-e2e         make e2e against the cluster (the stacks' ports forwarded to localhost)"
	@echo "  make k8s-stop-agents | k8s-resume-agents   stop-agents and resume-agents, on the cluster"
	@echo
	@echo "Starting over (deletes for good; lists what, then asks you to type yes):"
	@echo "  make backup DIR=d    copy their data and the keys to it into folder d (they stop meanwhile)"
	@echo "  make restore DIR=d   replace their data with a backup's (asks first; YES=1 skips)"
	@echo "  make wipe            delete their data: accounts, chats, sessions, emails, traces"
	@echo "  make distclean       wipe + delete every .env and settings file: back to a fresh clone"
	@echo "  make fresh           distclean, setup, up: a new install"
	@echo "  YES=1                don't ask (scripts, CI)"
	@echo
	@echo "Checks and design:"
	@echo "  make e2e             stacks, forgot password, passkeys, accessibility, in Chrome (stacks up)"
	@echo "  make evals           how dependably Gen9 does real tasks, kept in Langfuse (spends model calls;"
	@echo "                       SUITE=regression|research|canary TRIALS=3 TASK=\"id ...\" KEEP=1 keeps the chats)"
	@echo "  make evals-calibrate people score a sample of the rubric judge's verdicts in Langfuse (SAMPLE=20);"
	@echo "                       REPORT=1: how their scores compare with the judge's;"
	@echo "                       LABELS=file BY=who: reference labels not from people, reported apart"
	@echo "  make audit           known vulnerabilities in npm and Python dependencies, their signatures and provenance"
	@echo "  make sbom            an SBOM of every image the stacks build or run (scripts/sbom/out/)"
	@echo "  make scan            known vulnerabilities in those images: fails on a fixable high or critical one"
	@echo "  make updates         pinned images rebuilt under their tag since, and newer releases (Renovate)"
	@echo "  make design-sync     copy gen9-design (tokens, font, logo) into the apps"
	@echo "  make design-check    fail if an app's copy differs from gen9-design"
	@echo
	@echo "Example: make up STACKS=\"keycloak ui\"    make logs STACKS=ui FOLLOW=1"

stacks:
	@echo "Stacks, in start order. Pass any of them as STACKS=\"...\"; default is all."
	@echo
	@$(foreach s,$(ALL_STACKS),printf '  %-10s gen9-%-10s %s\n' '$(s)' '$(s)' '$(DESC_$(s))';)

doctor:
	@scripts/doctor.sh $(SELECTED)

admin-code:
	@scripts/admin-code.sh

# Gen9's own images: by digest from images.env when it exists (IMAGES=<lock> writes it, IMAGES=local
# removes it: scripts/images.sh), so nothing is built; else built here from each stack's folder
up:
	@scripts/doctor.sh --preflight $(RUNNING)
	@$(foreach s,$(SKIPPED),echo "note: gen9-$(s) isn't set up, so left out ($(DESC_$(s)))";)
	@$(if $(IMAGES),scripts/images.sh $(IMAGES))
	@$(if $(MISSING),printf 'Not set up yet. Missing:%b\nRun: make setup STACKS="%s"\n' \
	  "$(foreach f,$(MISSING),\n  $(f))" "$(call writers,$(MISSING))" >&2; exit 1)
	@case " $(SELECTED) " in *" models "*) \
	  grep -Eq '^[A-Z0-9_]+_API_KEY=.+' gen9-models/.env || \
	  echo "note: gen9-models/.env has no provider key, so no model can answer (make setup STACKS=models asks for an OpenAI key)" ;; esac
	@for p in $(OUTSIDE); do s=$${p%%:*} u=$${p#*:}; \
	  [ -n "$$(docker ps -q --filter label=com.docker.compose.project=gen9-$$u --filter label=com.docker.compose.oneoff --filter status=running)" ] || \
	  echo "note: gen9-$$s uses gen9-$$u, which isn't running (make up STACKS=\"$$u $$s\")"; done
	@for n in $(NETWORKS); do docker network inspect $$n >/dev/null 2>&1 || \
	  docker network create --label gen9.network=shared $$n >/dev/null || exit 1; done
	@# A container failing its health check (its database wiped, Keycloak down) is restarted first,
	@# so its checks start over: Compose's --wait fails at once on an unhealthy container it doesn't
	@# replace. Failing, not only unhealthy yet: one a failure short of it turned unhealthy during the
	@# wait and failed make up (gen9-learn's b7, manual-e2e.md P4-E5)
	@if [ -f images.env ]; then set -a; . ./images.env; set +a; how=--no-build; \
	  echo "Gen9's images: by digest (images.env; make up IMAGES=local builds them here)"; \
	else how=--build; fi; \
	for s in $(RUNNING); do \
	  echo "== gen9-$$s"; \
	  sick=$$(for c in $$(docker ps -q $(OWN)); do \
	    [ "$$(docker inspect -f '{{if .State.Health}}{{.State.Health.FailingStreak}}{{else}}0{{end}}' $$c)" = 0 ] || echo $$c; done); \
	  [ -z "$$sick" ] || { echo "restarting $$(docker inspect -f '{{.Name}}' $$sick | tr -d / | tr '\n' ' ')(failing its health check), so its checks start over"; \
	    docker restart $$sick >/dev/null; }; \
	  (cd gen9-$$s && docker compose up -d $$how --wait) || \
	  { echo "gen9-$$s didn't start. Its logs: make logs STACKS=$$s" >&2; exit 1; }; \
	done
	@$(list_urls)

# Every container of the project, so opt-in ones (e.g. gen9-ui's dev profile) stop too
down:
	@for s in $(call reverse,$(SELECTED)); do \
	  [ -n "$$(docker ps -aq $(OWN))" ] || { echo "== gen9-$$s: no containers"; continue; }; \
	  echo "== gen9-$$s"; $(call by_name,down) || exit 1; \
	done

ps:
	@for s in $(SELECTED); do \
	  [ -n "$$(docker ps -aq $(OWN))" ] || { echo "== gen9-$$s: no containers"; continue; }; \
	  echo "== gen9-$$s"; docker ps -a $(OWN) --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'; \
	done
	@$(list_urls)

logs:
	@if [ -n "$(FOLLOW)" ] && [ $(words $(SELECTED)) -ne 1 ]; then \
	  echo "FOLLOW=1 follows one stack: make logs STACKS=ui FOLLOW=1" >&2; exit 1; fi
	@for s in $(SELECTED); do \
	  [ -n "$$(docker ps -aq $(OWN))" ] || { echo "== gen9-$$s: no containers"; continue; }; \
	  echo "== gen9-$$s"; $(call by_name,logs --tail=$(TAIL) $(if $(FOLLOW),-f)) 2>&1 | $(as_text) || exit 1; \
	done

# Every profile, so opt-in services are checked too. Then, across all stacks: no stack may reach
# another under one of its own service names on a shared network (scripts/check-networks.py), and
# docker-bake.hcl must build each Gen9 image as the Compose files do (scripts/check-images.py), and
# Gen9's packages must declare one version (scripts/check-version.py).
config:
	@status=0; $(foreach s,$(SELECTED),printf '== gen9-$(s): '; \
	  $(if $(filter $(s),$(SKIPPED)),echo "not set up (optional)";, \
	  $(if $(call missing,$(s)),echo "not set up: missing $(call missing,$(s))"; status=1;, \
	  (cd gen9-$(s) && docker compose --profile '*' config --quiet) && echo ok || status=1;))) \
	  exit $$status
	@if command -v python3 >/dev/null; then scripts/check-networks.py && scripts/check-images.py && scripts/check-version.py; \
	  else echo "python3 not found: skipped the shared-network name and image checks"; fi

# What runs against what's declared: Compose's hash of each service, its image, what docker update
# changes, containers missing or not declared (scripts/drift.py). reset recreates what differs
diff:
	@scripts/drift.py $(RUNNING)

reset:
	@scripts/drift.py --reset $(RUNNING)

# Kubernetes (docs/operations.md, "Kubernetes"): each stack a Helm release of gen9-<stack>/chart in
# its namespace, with the lock's images (images.env, as IMAGES=… writes it) and its settings files
# as Secrets (scripts/k8s.sh)
k8s-up:
	@$(if $(IMAGES),scripts/images.sh $(IMAGES))
	@scripts/k8s.sh up $(SELECTED)

k8s-diff:
	@scripts/k8s.sh diff $(SELECTED)

k8s-reset:
	@scripts/k8s.sh reset $(SELECTED)

k8s-down:
	@scripts/k8s.sh down $(SELECTED)

k8s-e2e:
	@scripts/k8s.sh e2e $(SELECTED)

k8s-stop-agents:
	@scripts/k8s.sh stop-agents

k8s-resume-agents:
	@scripts/k8s.sh resume-agents

# The preflight but of the optional stacks: setup writes their files, and make up checks their ports
# once they run, so a domain (or DOMAIN=localhost, which unsets gen9-edge) isn't stopped by what
# holds ports 80 and 443 meanwhile; nor what setup itself writes (an older .env's COMPOSE_PROFILES)
setup:
	@scripts/doctor.sh --preflight --before-setup $(filter-out $(OPTIONAL),$(SELECTED))
	@scripts/setup.sh $(SELECTED)

# A cold backup of the stacks' volumes and settings files (scripts/backup.sh), and its restore
backup:
	@if [ -n "$(INTO)" ]; then scripts/backup.sh --into "$(INTO)" --keep "$(or $(KEEP),7)" $(SELECTED); \
	elif [ -n "$(DIR)" ]; then scripts/backup.sh "$(DIR)" $(SELECTED); \
	else echo "Name a new folder for the backup: make backup DIR=~/gen9-backup-$$(date +%F), or keep the last few: make backup INTO=~/gen9-backups KEEP=7"; exit 2; fi

# Every agent at once (gen9-agent's stop.py; manual-e2e.md, P5-C10): the runs end, recorded, before
# the worker stops; what people ask meanwhile waits, and runs once resumed
stop-agents:
	@cd gen9-agent && docker compose exec -T worker gen9-agent-stop && docker compose stop worker

resume-agents:
	@cd gen9-agent && docker compose up -d --wait worker && docker compose exec -T worker gen9-agent-stop --resume

restore:
	@[ -n "$(DIR)" ] || { echo "Name the backup's folder: make restore DIR=..."; exit 2; }
	@scripts/restore.sh $(WIPE_FLAGS) "$(DIR)"

wipe:
	@scripts/wipe.sh $(WIPE_FLAGS) $(SELECTED)

distclean:
	@scripts/wipe.sh --secrets $(WIPE_FLAGS) $(SELECTED)

# One confirmation (distclean's); command-line variables (STACKS, YES…) reach the sub-makes
fresh:
	@$(MAKE) --no-print-directory distclean
	@$(MAKE) --no-print-directory setup
	@$(MAKE) --no-print-directory up

design-sync:
	@gen9-design/sync.sh
design-check:
	@gen9-design/sync.sh --check

# With images.env, the lock's images too: context and fairness start a worker with docker compose run
e2e:
	@if [ -f images.env ]; then set -a; . ./images.env; set +a; fi; \
	cd e2e && npm ci --silent && npm run -s stacks && npm run -s temporal && npm run -s runs && npm run -s models && npm run -s search && npm run -s memory && npm run -s skills && npm run -s agents && npm run -s questions && npm run -s approvals && npm run -s retry && npm run -s connectors && npm run -s connectors-oauth && npm run -s connectors-keycloak && npm run -s directory && npm run -s elicitation && npm run -s apps && npm run -s tool-changes && npm run -s environments && npm run -s scheduled && npm run -s triggers && npm run -s notifications && npm run -s outcomes && npm run -s background && npm run -s mcp-server && npm run -s agui && npm run -s a2a && npm run -s context && npm run -s past-chats && npm run -s memory-controls && npm run -s authz && npm run -s standing && npm run -s stop && npm run -s database && npm run -s audit && npm run -s admin-api && npm run -s demotion && npm run -s export && npm run -s cross-site && npm run -s fairness && npm run -s plugins && npm run -s plugins-conformance && npm run -s recovery && npm run -s lockout && npm run -s oauth && npm run -s passkeys && npm run -s keyboard && npm run -s focus && npm run -s a11y

# The evals (gen9-agent/README.md, "Evals"): the seeded user signs in to a temporary config
# directory (e2e/token.mjs), and a suite runs through the API, each task TRIALS times, as a
# Langfuse experiment. Spends model calls, so nothing runs it on its own. Its judge calls the router
# with a key of its own (models-evals.local.env): the chat alias only, within a daily budget.
SUITE  ?= regression
TRIALS ?= 3
evals:
	@[ -f gen9-agent/models-evals.local.env ] || { echo "No gen9-agent/models-evals.local.env (the judge's router key): run make setup STACKS=models, then make up STACKS=models"; exit 1; }
	@[ -d e2e/node_modules ] || (cd e2e && npm ci --silent)
	@dir=$$(mktemp -d) && trap 'rm -rf "$$dir"' EXIT && \
	  GEN9_CONFIG_DIR=$$dir node e2e/token.mjs user && \
	  cd gen9-agent && GEN9_CONFIG_DIR=$$dir uv run -q --env-file langfuse.local.env \
	    --env-file models-evals.local.env python -m evals --suite $(SUITE) --trials $(TRIALS) \
	    $(foreach t,$(TASK),--task $(t)) $(if $(filter 1,$(KEEP)),--keep)

# Calibrating the rubric judge against people (gen9-agent/README.md, "Evals"): SAMPLE of its
# recent verdicts go to Langfuse's "Judge calibration" annotation queue, half met and half not;
# REPORT=1 compares people's scores there with the judge's. Needs only Langfuse's keys.
SAMPLE ?= 20
evals-calibrate:
	@cd gen9-agent && uv run -q --env-file langfuse.local.env python -m evals.calibrate \
	  $(if $(LABELS),--label "$(abspath $(LABELS))" --by "$(BY)",$(if $(filter 1,$(REPORT)),--report,--sample $(SAMPLE)))

# Fails on high or critical advisories, and, where a project's dependencies are installed, on one
# whose registry signature doesn't verify. The images' own packages and runtimes: make sbom, make scan
audit:
	@for d in gen9-ui gen9-keycloak/theme e2e scripts/updates; do echo "== $$d"; (cd $$d && node $(CURDIR)/scripts/npm-audit.mjs && \
	  { [ ! -d node_modules ] || npm audit signatures; }) || exit 1; done
	@for d in gen9-agent gen9-cli; do echo "== $$d"; f=$$(mktemp); \
	  (cd $$d && uv export --frozen --no-hashes --no-emit-project --color never > $$f) && \
	  NO_COLOR=1 uvx pip-audit -r $$f --disable-pip --no-deps --progress-spinner off; \
	  status=$$?; rm -f $$f; [ $$status -eq 0 ] || exit $$status; done
	@echo "== PyPI provenance (scripts/provenance.py)"; uv run -q scripts/provenance.py

# Each image's SBOM, and Grype's scan of them, with pinned and verified Syft and Grype run in a
# container with no Docker socket (scripts/sbom.sh; docs/operations.md)
sbom:
	@STACKS="$(SELECTED)" scripts/sbom.sh sbom
scan:
	@scripts/sbom.sh scan

# Renovate's dry run over the stacks' compose files and Dockerfiles; changes nothing (scripts/updates.sh)
updates:
	@scripts/updates.sh
