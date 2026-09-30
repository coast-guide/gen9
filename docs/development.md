# Developing Gen9

How the repository is organised for the people and AI agents who change it, how changes are checked,
and how to add a stack. The rules themselves (how work is planned, verified and committed, and
where each kind of knowledge lives) are in [AGENTS.md](../AGENTS.md); how to propose a change is in
[CONTRIBUTING.md](../CONTRIBUTING.md).

## Working on Gen9

Work that spans sessions has a plan in `docs/plans/` ([how plans work](PLANS.md)); which one is
active, and how every session starts, is in [AGENTS.md, "Start of every session"](../AGENTS.md#start-of-every-session).

## Checks

Which checks each part of the repository needs before a commit: [AGENTS.md, "Checks"](../AGENTS.md#checks).

Design system: `make design-sync` copies `gen9-design` into the apps; `make design-check` fails if a copy drifted.

CI (`.github/workflows/checks.yml`) runs on every pull request and on `main`. It checks: types, lint, unit tests, dependency advisories, the design-token copies, gen9-agent's plugin loader against the Agent Plugins conformance kit and, on Linux with real Docker, the make workflow itself (shellcheck, `setup` without a terminal, `doctor`, `config`, then gen9-postgres, gen9-keycloak and gen9-agent up and calling each other, `verify.sh`, gen9-models serving its aliases to gen9-agent's key and to no other stack's network, gen9-temporal up with its namespace, its frontend refusing callers without a token and its internal ports callers without its certificate, and starting over). `make e2e` needs Chrome and every stack, and runs locally.

Dependencies: `make audit` fails on high or critical advisories in the npm projects and the locked Python packages (gen9-agent, gen9-cli). Images: `docker scout cves --only-fixed --only-severity critical,high <image>` (Docker Desktop); a scanner reports a Go binary by its Go version, so check one with `govulncheck -mode binary <file>`, which looks for the vulnerable functions themselves. The last review of every image, finding by finding, is in `docs/plans/manual-e2e.md` (P4-A5). Every image is pinned as `tag@digest`, so a tag rebuilt with its base image's fixes, or a newer release, goes unnoticed: `make updates` lists both, from Renovate's dry run over a copy of the compose files and Dockerfiles (Node.js, about a minute; it changes nothing).

End to end: `make e2e` drives real Chrome against every running stack and checks what a person and an admin can do and what they must not be able to do: signing in and out, chats that keep going without anyone watching, questions and approvals, models through the router within budgets, connectors and their apps, a chat's environment, background and scheduled tasks, Gen9 as an MCP server, an AG-UI and an A2A agent, search, memory, plugins, access control between people and for admins, stopping every agent, data export, the audit record, fairness between people, passkeys and account recovery, keyboard use, and accessibility on every screen. Each check, step by step, and how to run it in Firefox or WebKit: [e2e/README.md](../e2e/README.md).

Evals: `make evals` measures how dependably Gen9 does real tasks. It runs a suite of tasks through gen9-agent's API as the seeded user, each several times in fresh chats. Code graders, and a model judge where code can't decide, check what each run produced, and every run of a suite is kept in Langfuse as an experiment with its pass rates (`pass@k`, `pass^k`). It spends model calls, so it runs only when you ask (see [gen9-agent/README.md](../gen9-agent/README.md#evals)). `make evals-calibrate` sends a sample of the judge's verdicts to people in Langfuse, and `make evals-calibrate REPORT=1` compares their scores with the judge's.

## How stacks stay decoupled

`make up` and `make config` run `cd gen9-<name> && docker compose …` for each stack, so each keeps its own project name, `.env`, volumes and network, the same as running `docker compose` inside the folder. Both ways manage the same containers. `down`, `ps`, `logs`, `wipe` and `distclean` find a stack's containers, volumes and networks by the project label Compose puts on them (`com.docker.compose.project=gen9-<name>`), so they work even when its setup files are gone; a container counts as the stack's only if it also has Compose's `com.docker.compose.oneoff` label, as Compose itself counts them (the containers of an image Compose built carry its project label too, like OpenSandbox's egress sidecars from gen9-sandbox's image). Stacks share nothing but the `*.local.env` files one stack's setup writes into another's folder, and the per-stack networks below.

Stacks are deliberately not merged with Compose [`include`](https://docs.docker.com/reference/compose-file/include/): that loads everything into one project, which renames every volume (orphaning existing data) and silently keeps only one of two same-named services (for example two `postgres`).

Clients on the host (browser, `gen9-cli`) reach stacks at `localhost:<published port>`. Containers reach another stack over that stack's network, `gen9-<stack>`, at `gen9-<stack>:<container port>`: for example `gen9-postgres:5432`, `gen9-keycloak:8080`, `gen9-temporal:7233`, `gen9-agent:8000`. Each stack joins its own network (with the alias `gen9-<stack>`; gen9-langfuse also puts its media store there, `gen9-langfuse-media`, where gen9-agent's worker uploads a trace's images) and the networks of the stacks it calls, nothing more: gen9-agent joins `gen9-postgres`, `gen9-keycloak`, `gen9-langfuse` and `gen9-temporal`, and `gen9-models` (the worker runs the agent's calls; the API only embeds and reranks search queries, with a key that can do nothing else), gen9-ui joins `gen9-keycloak` and `gen9-agent`, gen9-temporal joins `gen9-keycloak` (Keycloak's keys for tokens, and its web UI's sign-in), gen9-models joins no other stack's network (it reaches model providers over the internet, and only gen9-agent's worker reaches it), and gen9-keycloak joins `gen9-ui` for back-channel logout. So gen9-ui can't reach the database at all. Reaching a stack isn't trusting it: gen9-agent checks tokens, and Temporal's frontend does too, while its internal ports require its own certificate (gen9-temporal/README.md, "Security"). The networks are declared `external`, so no stack owns another's; `make up` creates them (by hand: `docker network create gen9-<stack>`), and `make wipe` removes one once no stack uses it.

This works the same on Linux and Docker Desktop. Going through the host instead (`host.docker.internal` to ports published on `127.0.0.1`) only works on Docker Desktop: on a Linux engine a container can't reach a port published on the host's loopback, and publishing on every interface would expose the databases (Docker's published ports bypass ufw). One network shared by all stacks is out too: Docker registers every service name on every network the service joins, and a container asking for its own `postgres` got the other stack's `postgres` there. `make config` fails if a change would let that happen (`scripts/check-networks.py`).

## Add a stack

1. Create `gen9-<name>/` with a Compose file and a README; publish ports on `127.0.0.1` in the next free block (`15000–15099`, …).
2. In the `Makefile`, add `<name>` to `ALL_STACKS` after the stacks it needs, describe it in `DESC_<name>` and list the files it can't start without in `NEEDS_<name>`. If it generates files, teach `scripts/setup.sh` and `scripts/wipe.sh` about them.
3. Check with `make config STACKS=<name>`, then `make up STACKS=<name>`.
