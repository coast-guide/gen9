
# gen9

Gen9 is an agent platform you host yourself: a general-purpose AI agent your team gives tasks to, from the browser or the terminal. It plans the work, uses the tools and services you connect, runs code in a sandbox of its own, asks before it changes anything, and keeps going after the tab is closed. What it is good at is yours to set: skills, connectors, helpers and plugins.

It is built as a set of decoupled services, one folder each. Every Docker-based service is its own Docker Compose stack; the root `Makefile` starts and stops them together.

## Stacks

| Folder            | What                                                                      | Host ports (`127.0.0.1`) |
| ----------------- | ------------------------------------------------------------------------- | -------------------------- |
| `gen9-langfuse` | Self-hosted Langfuse (tracing, evals)                                     | `13000–13007`           |
| `gen9-ui`       | Next.js web app, auth BFF (`prod`; `dev` on demand), connectors' app sandbox + Valkey sessions | `14000–14003`           |
| `gen9-keycloak` | Keycloak identity provider (own Postgres, Mailpit, Gen9 theme)            | `15000–15003`           |
| `gen9-postgres` | Postgres 18 + pgvector + pg_textsearch (BM25), the application database  | `16000`                  |
| `gen9-agent`    | Agent API (FastAPI + Deep Agents, Keycloak-protected) and the workers that run it | `17000`                  |
| `gen9-temporal` | Temporal: durable execution for runs, approvals, schedules, deletions (own Postgres, web UI) | `18000–18001`           |
| `gen9-models`   | The model router: every model by alias, wherever hosted (LiteLLM Proxy, own Postgres, Gen9's admin API) | `19000–19001`           |
| `gen9-sandbox`  | Environments: OpenSandbox runs each chat's commands and files in containers of its own | `20000`                  |

`gen9-learn` is everything about Gen9 in one page, for anyone starting from nothing (open `gen9-learn/index.html`: what Gen9 is and how it's built, then one user from sign-up to deletion watched in DevTools, every store and the logs, with deeper steps for the rest, and a Reference that names every service, workflow, route, table, setting and command, checked against the running system), `gen9-design` is the design system (tokens, font, logo) that the apps copy in, `gen9-cli` is a terminal client (`gen9 login` signs in with a one-time code confirmed in a browser, then `gen9 ask`), and `e2e` holds end-to-end checks in real Chrome (calls between stacks, passkeys, accessibility); none of them is a stack. How users, sign-in and tokens work across the stacks: [docs/auth-architecture.md](docs/auth-architecture.md). Where Gen9 uses Temporal, and the rules for it: [docs/temporal.md](docs/temporal.md).

## First-time setup

Each stack generates its own secrets; Keycloak, Postgres and Langfuse also write the settings the apps need. One command does it for every stack and never overwrites anything:

```bash
make setup     # generates what is missing, asks for what it can't generate
make up
```

`make setup` asks for three things. Two are provider keys, which it saves in `gen9-models/.env` (readable only by you): OpenRouter's, for `chat`, vision and embeddings, and OpenAI's, for speech and images. gen9-agent reaches every model through the router (gen9-models) by alias and holds no provider key; other providers' keys go there too (gen9-models/README.md). The third is Langfuse's first user, the account you sign in to Langfuse with. Without a terminal it asks nothing: pass them as `make setup OPENROUTER_API_KEY=… OPENAI_API_KEY=… LANGFUSE_EMAIL=you@example.com LANGFUSE_NAME="Your Name"` (or export them), or put the keys in `gen9-models/.env` yourself; it says which are missing. Langfuse's project keys go to `gen9-agent/langfuse.local.env`, which gen9-agent reads before its `.env`.

`make setup` is safe to rerun: it keeps every `.env`, and if a settings file one stack wrote for another is gone (say `gen9-agent/keycloak.local.env`), it rebuilds it from that stack's `.env` (`init-env.sh --from-env`) without generating new secrets, so nothing is lost. `gen9-agent/langfuse.local.env` is the exception, since it overrides keys you may have put in `gen9-agent/.env`: rebuild it with `gen9-langfuse/init-env.sh --from-env --agent-env-file ../gen9-agent/langfuse.local.env`.

Then open http://localhost:14000 and sign in as `ada@gen9.test` (admin) or `alan@gen9.test`, with passwords from `grep ^GEN9_SEED_ gen9-keycloak/.env`, or create an account (emails arrive in Mailpit at http://localhost:15002).

## Upgrade

1. `make backup DIR=~/gen9-backup-before-upgrade`, to go back if you need to: an older Gen9 refuses a database a newer one migrated (its `/readyz` says so), and an older ClickHouse may not open the traces a newer one wrote (gen9-langfuse/README.md).
2. `git pull`, or check out the version you want.
3. `make setup`: it adds what the new version needs (new settings, a database role, a budget) and keeps every secret you have.
4. `make up`: it builds the new images, applies the database's migrations before the agent starts, then replaces the containers. A turn that is running when its worker is replaced goes on in the new one, from its last checkpoint; the web app and the terminal say it restarted.

## Back up and restore

| Command | Does |
| --- | --- |
| `make backup DIR=~/gen9-backup` | Stops the stacks, copies every data volume (accounts and passwords, chats, memory, files, schedules and workflow state, traces, the router's budgets and spend, sessions, test emails) and the settings files that hold their keys into a new folder only you can read, then starts them again. About 4 minutes and 1 GB for a small install |
| `make backup INTO=~/gen9-backups KEEP=7` | The same, into a new time-stamped folder under `INTO` (`gen9-backup-<UTC time>`), then keeps only the newest `KEEP` complete backups there (default 7): older ones, and backups that didn't finish, are removed. Only folders a Gen9 backup made are touched. For a schedule |
| `make restore DIR=~/gen9-backup` | Replaces what the stacks hold now with the backup, keys included, and starts them. Asks you to type `yes` (`YES=1` skips) |

A backup is a cold copy of each volume (Docker's "Back up, restore, or migrate data volumes"; Langfuse's guide for Docker installs stops ClickHouse the same way), so it holds everything and is consistent, and restores into the same version of Gen9: moving to a new major version of Postgres takes `pg_dump` (each stack's README). The settings files go with the data because the data is encrypted with their keys (docs/secrets.md), which makes the folder as secret as your `.env` files. Left out: the local profile's downloaded models (downloaded again) and the chats' running environments (temporary; what a chat shared from one is in the database). `STACKS` limits both, as with every command. The scripts are `scripts/backup.sh` and `scripts/restore.sh`.

The backup's folder has to be one Docker can mount. Docker Desktop shares only some of the host's paths with containers (your home folder is one; Settings > Resources > File sharing), so `~/gen9-backup` works there and a folder under `/tmp` may not. `make backup` and `make restore` find that out first: if Docker can't mount the folder they say so and change nothing, before any stack stops or any data goes.

On a schedule, run `make backup INTO=… KEEP=…` from the host's own timer, at a quiet hour: each run stops the stacks for a few minutes (about 4 for a small install). For example, cron, every night at 03:00: `0 3 * * * cd /path/to/gen9 && make backup INTO=/srv/gen9-backups KEEP=7 >> /var/log/gen9-backup.log 2>&1`. Put `INTO` on another disk or machine too, if a backup is to outlive this one.

A backup also holds the people and chats deleted after it was made, which is why the deletion dialogs mention it. On a schedule with `KEEP`, a deleted person is gone from the backups once `KEEP` newer ones have been made (a week, nightly with `KEEP=7`). Otherwise, keep backups only as long as you need them, and delete old folders: the UK regulator's guidance on erasure (ICO, "Right to erasure") asks that backup data stay "beyond use" until it is replaced. Restoring one puts it back into use, so `make restore` deletes again what was deleted after the backup was made. It reads which accounts and chats from Gen9's audit record (deleted by the person, an admin, the sweep of users deleted in Keycloak, or an earlier restore) and runs the same deletions (`gen9-agent-erase` in gen9-agent's worker). Sometimes the record can't be read, or it starts after the backup, because gen9-postgres was made again since. Then it says so and deletes nothing, and you delete again by id: `(cd gen9-agent && docker compose exec worker gen9-agent-erase --users SUB... --threads ID...)`.

## Stop every agent at once

If Gen9's agents must do nothing more right now (a runaway task, a leaked account, an injected
instruction you don't trust):

| Command | Does |
| --- | --- |
| `make stop-agents` | Cancels every run not yet over (queued, running or waiting for someone), waits until each has ended, pauses every scheduled task, then stops gen9-agent's worker. The web app and the API stay up: people can read their chats, and what they ask meanwhile waits |
| `make resume-agents` | Starts the worker again, and unpauses the scheduled tasks the stop paused. A task its person paused stays paused. What people asked while stopped then runs |

Both are in the audit log (`operator.stop`, `operator.resume`). For one person, disable their
account on Admin > Users: their runs end at once. Disabling them in Keycloak's own console ends
their turns within a minute (docs/plans/manual-e2e.md, P5-C10).

## Start over

These delete for good. Each lists what it will delete and asks you to type `yes`. `YES=1` skips the question (for scripts; without a terminal they refuse rather than wait).

| Command            | Deletes                                                                                                     | Keeps                                        |
| ------------------ | ----------------------------------------------------------------------------------------------------------- | -------------------------------------------- |
| `make down`      | Nothing: stops containers                                                                                   | Data and secrets                             |
| `make wipe`      | Containers and data volumes: accounts, passwords, passkeys, chats, sessions, test emails, traces, workflow state | Every `.env` and settings file, images, the local profile's downloaded models |
| `make distclean` | `wipe` plus every `.env` and settings file (each stack's `.env`, the `*.local.env` files, and `gen9-agent/.env`; `make setup` asks for your provider keys again, as they were in `gen9-models/.env`): a fresh clone | Images, the local profile's downloaded models |
| `make fresh`     | `distclean`, then `setup` and `up`: a new install, one confirmation                                   | Same as distclean                            |

Like every command, they cover all stacks unless you pass `STACKS`: `make wipe STACKS=ui` signs everyone out and touches nothing else. When a running stack uses one you delete (gen9-agent on gen9-postgres), `wipe` names it and prints the `make up` that starts both again: the agent then re-applies its migrations to the new database, and until then its `/readyz` says it isn't ready. The scripts are `scripts/wipe.sh` and `scripts/setup.sh`.

## Run everything

**Requires:** Docker with Compose 2.24 or newer, GNU Make (macOS's built-in 3.81 is enough), bash, openssl. With gen9-langfuse, give Docker at least 8 GiB of memory: all stacks idle used 5.8 GiB after a day of use (Langfuse 2.9, Keycloak 1.4, the model router 0.6, Temporal 0.5, measured). `make doctor` checks all of this and the ports each stack needs; `make setup` and `make up` run the same checks first and stop before starting anything. It stops a start if a secret is missing from gen9-langfuse/.env (Langfuse's own compose file would fall back to a published default). On a running install it also checks Temporal's certificate, and warns of a deletion still running after a day (a step that keeps failing).

```bash
make            # list commands
make stacks     # list the stacks
make up         # start every stack, wait until healthy, then list where to open each
make ps         # containers of every stack, and where to open them
make logs       # last 50 log lines of every stack
make down       # stop every stack (data volumes are kept)
```

Every command covers all stacks. `STACKS` narrows it to some, by name from `make stacks` (`gen9-ui` works too); they start in dependency order and stop in reverse, whatever order you list them in:

```bash
make up STACKS="keycloak ui"
make logs STACKS=ui FOLLOW=1      # follow one stack's logs; TAIL=200 for more lines
make config STACKS=agent          # validate its Compose file
```

Design system: `make design-sync` copies `gen9-design` into the apps; `make design-check` fails if a copy drifted.

CI (`.github/workflows/checks.yml`) runs on every pull request and on `main`. It checks: types, lint, unit tests, dependency advisories, the design-token copies, gen9-agent's plugin loader against the Agent Plugins conformance kit and, on Linux with real Docker, the make workflow itself (shellcheck, `setup` without a terminal, `doctor`, `config`, then gen9-postgres, gen9-keycloak and gen9-agent up and calling each other, `verify.sh`, gen9-models serving its aliases to gen9-agent's key and to no other stack's network, gen9-temporal up with its namespace, its frontend refusing callers without a token and its internal ports callers without its certificate, and starting over). `make e2e` needs Chrome and every stack, and runs locally.

Dependencies: `make audit` fails on high or critical advisories in the npm projects and the locked Python packages (gen9-agent, gen9-cli). Images: `docker scout cves --only-fixed --only-severity critical,high <image>` (Docker Desktop); a scanner reports a Go binary by its Go version, so check one with `govulncheck -mode binary <file>`, which looks for the vulnerable functions themselves. The last review of every image, finding by finding, is in `docs/plans/manual-e2e.md` (P4-A5). Every image is pinned as `tag@digest`, so a tag rebuilt with its base image's fixes, or a newer release, goes unnoticed: `make updates` lists both, from Renovate's dry run over a copy of the compose files and Dockerfiles (Node.js, about a minute; it changes nothing).

End to end: `make e2e` makes every call between stacks the way a user does (sign in, chat, trace, back-channel logout), checks that Temporal's web UI lets in only Gen9 admins and decrypts payloads for them, that model calls go through the router under the user (priced in Langfuse, refused with a clear message over budget), that an answer keeps going through a reload, while nobody watches, and stops when you press Stop, that a question the agent asks mid-task waits across a worker restart until you answer it in Chrome or the terminal, that in a chat set to "Ask before acting" a memory write waits for your Allow or Deny, that an answer whose model provider stopped answering waits for Retry and then continues where it was, that a remote MCP server connected in Settings lends the agent its tools and asks before each use, and that tools its server changes later wait for the person's look, that a connector's app (its View) and its questions can't decide for the person, that a chat's environment runs commands and keeps files, sends a person's secrets only to their hosts and for what they allowed, and stays within its disk, that background tasks run beside a chat and tell it when they end, that Gen9 serves other programs as an MCP server, an AG-UI agent and an A2A agent (an A2A agent reaching only the chats it started), that a long chat is summarized without losing what matters, that past chats are searched and cited, and memory's switches hold, that an admin adds plugin marketplaces from git, reads what a plugin's skills say and chooses who may have each plugin, and that a plugin changed since waits for them, that a scheduled task fires on its own (once, and by its Schedule), pauses, resumes and runs now, and fires over HTTP with its own token (text labelled as data, hourly limits), that a task's run emails its person once when done or needing them (as they chose in Settings), that every route of gen9-agent's API refuses requests without a token, admin routes to people who aren't admins, and one person's ids to another, that nothing is done any more for a person disabled in Keycloak or in Gen9 (their task's trigger, its Schedule, a queued message, a turn under way), that `make stop-agents` stops every agent until `make resume-agents`, that an admin whose access is removed loses it at once, that another site can't read or change a signed-in person's Gen9, that a person's data downloads in full, that Gen9 records who did what (an admin's role change with the admin as actor, where Keycloak sees only Gen9's service account; people's security actions; refused access) in a record nobody can change, that with one agent slot a chat run goes before queued background runs and one person's many don't hold back another's, that *Forgot password* asks for an authenticator code before a new password, adds a passkey, signs in with it and removes it, that Keycloak's sign-in, authenticator code and recovery code work by keyboard alone, then audits every screen for accessibility, in Chrome against the running stacks (see [e2e/README.md](e2e/README.md)). The checks drive Chrome; `GEN9_BROWSER=firefox FIREFOX_PATH=…` runs them in Firefox, skipping what it can't automate, and `npm run webkit` checks WebKit, Safari's engine (e2e/README.md, "Other browsers").

Evals: `make evals` measures how dependably Gen9 does real tasks. It runs a suite of tasks through gen9-agent's API as the seeded user, each several times in fresh chats. Code graders, and a model judge where code can't decide, check what each run produced, and every run of a suite is kept in Langfuse as an experiment with its pass rates (`pass@k`, `pass^k`). It spends model calls, so it runs only when you ask (see [gen9-agent/README.md](gen9-agent/README.md#evals)). `make evals-calibrate` sends a sample of the judge's verdicts to people in Langfuse, and `make evals-calibrate REPORT=1` compares their scores with the judge's.

Each stack labels the services you open (`gen9.name`, `gen9.url` in its Compose file), from the same settings the stack itself uses, so the list stays right when you change a port. `make up` also notes when a stack it starts calls another that isn't running, and restarts a container failing its health check, unhealthy or on its way there (the agent's API after its database was wiped, say), before waiting for it: Compose's `--wait` fails at once on an unhealthy container it doesn't replace (docker/compose#9092), while a restarted one gets its start period again.

Each stack's own setup (its `init-env.sh` options, such as other ports) is in its README; `make setup` runs them with the defaults. `make up` refuses to start a stack whose files are missing and says which `make setup` writes them.

## Disk

What grows with use, and what keeps it bounded (measured after a day: every volume under 200 MB):

| Store | Grows with | Bounded by |
| --- | --- | --- |
| gen9-postgres (chats, runs and their events, memory, plugin files) | People's chats and plugins | Nothing on its own: chats are people's, kept until they or an admin delete them (which also removes their traces and environments) |
| gen9-langfuse ClickHouse and MinIO (traces) | Every run | Deleting chats and accounts erases their traces. Langfuse's raw copies of what it ingests (MinIO's `events/`) expire after a day. Langfuse's time-based retention is its Enterprise Edition's when self-hosted. ClickHouse's own logs are bounded (`gen9-langfuse/clickhouse/disk.xml`) |
| gen9-temporal (workflow histories) | Every run and task | Closed workflows are kept 72 hours (the namespace's retention) |
| gen9-keycloak's Mailpit (test emails) | Emails sent | 5,000 messages (`MP_MAX_MESSAGES`) |
| gen9-models (spend log) | Every model call | Nothing on its own; an account's erasure deletes its rows |
| Every container's log | Requests, and Keycloak's sign-in failures (the user and their address) | Three files of 10 MB a container, compressed, the oldest lines going first (Docker's `local` driver, `x-logging` in each Compose file, and gen9-sandbox's `launch.py` for the chats' environments; Docker's default keeps a log unbounded) |
| A chat's environment (its container's writable layer) | What its commands write | `SANDBOX_DISK_GB` (10) each: one past it is deleted (gen9-sandbox's `launch.py`), and each ends 30 minutes after its last use |

`docker system df -v` lists each volume's size.

## How stacks stay decoupled

`make up` and `make config` run `cd gen9-<name> && docker compose …` for each stack, so each keeps its own project name, `.env`, volumes and network, the same as running `docker compose` inside the folder. Both ways manage the same containers. `down`, `ps`, `logs`, `wipe` and `distclean` find a stack's containers, volumes and networks by the project label Compose puts on them (`com.docker.compose.project=gen9-<name>`), so they work even when its setup files are gone; a container counts as the stack's only if it also has Compose's `com.docker.compose.oneoff` label, as Compose itself counts them (the containers of an image Compose built carry its project label too, like OpenSandbox's egress sidecars from gen9-sandbox's image). Stacks share nothing but the `*.local.env` files one stack's setup writes into another's folder, and the per-stack networks below.

Stacks are deliberately not merged with Compose [`include`](https://docs.docker.com/reference/compose-file/include/): that loads everything into one project, which renames every volume (orphaning existing data) and silently keeps only one of two same-named services (for example two `postgres`).

Clients on the host (browser, `gen9-cli`) reach stacks at `localhost:<published port>`. Containers reach another stack over that stack's network, `gen9-<stack>`, at `gen9-<stack>:<container port>`: for example `gen9-postgres:5432`, `gen9-keycloak:8080`, `gen9-temporal:7233`, `gen9-agent:8000`. Each stack joins its own network (with the alias `gen9-<stack>`; gen9-langfuse also puts its media store there, `gen9-langfuse-media`, where gen9-agent's worker uploads a trace's images) and the networks of the stacks it calls, nothing more: gen9-agent joins `gen9-postgres`, `gen9-keycloak`, `gen9-langfuse` and `gen9-temporal`, and `gen9-models` (the worker runs the agent's calls; the API only embeds and reranks search queries, with a key that can do nothing else), gen9-ui joins `gen9-keycloak` and `gen9-agent`, gen9-temporal joins `gen9-keycloak` (Keycloak's keys for tokens, and its web UI's sign-in), gen9-models joins no other stack's network (it reaches model providers over the internet, and only gen9-agent's worker reaches it), and gen9-keycloak joins `gen9-ui` for back-channel logout. So gen9-ui can't reach the database at all. Reaching a stack isn't trusting it: gen9-agent checks tokens, and Temporal's frontend does too, while its internal ports require its own certificate (gen9-temporal/README.md, "Security"). The networks are declared `external`, so no stack owns another's; `make up` creates them (by hand: `docker network create gen9-<stack>`), and `make wipe` removes one once no stack uses it.

This works the same on Linux and Docker Desktop. Going through the host instead (`host.docker.internal` to ports published on `127.0.0.1`) only works on Docker Desktop: on a Linux engine a container can't reach a port published on the host's loopback, and publishing on every interface would expose the databases (Docker's published ports bypass ufw). One network shared by all stacks is out too: Docker registers every service name on every network the service joins, and a container asking for its own `postgres` got the other stack's `postgres` there. `make config` fails if a change would let that happen (`scripts/check-networks.py`).

## Working on Gen9

Rules for people and AI agents working on this repository, and where each kind of knowledge lives:
[AGENTS.md](AGENTS.md). Work that spans sessions has a plan in `docs/plans/` ([how plans
work](docs/PLANS.md)); the current one is [docs/plans/gen9-learn.md](docs/plans/gen9-learn.md)
(`harness.md` is complete, and `manual-e2e.md` is paused).

## Add a stack

1. Create `gen9-<name>/` with a Compose file and a README; publish ports on `127.0.0.1` in the next free block (`15000–15099`, …).
2. In the `Makefile`, add `<name>` to `ALL_STACKS` after the stacks it needs, describe it in `DESC_<name>` and list the files it can't start without in `NEEDS_<name>`. If it generates files, teach `scripts/setup.sh` and `scripts/wipe.sh` about them.
3. Check with `make config STACKS=<name>`, then `make up STACKS=<name>`.

## Licence

Apache License 2.0 ([LICENSE](LICENSE)). [NOTICE](NOTICE) names the material from other projects that the repository carries: the typeface, the Keycloak theme's starter, the copied UI components, the plugin schemas.
