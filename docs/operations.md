# Operating Gen9

How to install Gen9 on your own machine or server, run it day to day, upgrade it, back it up, stop
its agents, and start over. What each stack does is in its own README (the table in the
[README](../README.md#how-it-is-built) links to each); every command below is a `make` target
(`make` lists them).

## Requirements

Docker with Compose 2.24 or newer, GNU Make (macOS's built-in 3.81 is enough), bash, openssl. With gen9-langfuse, give Docker at least 8 GiB of memory: all stacks idle used 5.8 GiB after a day of use (Langfuse 2.9, Keycloak 1.4, the model router 0.6, Temporal 0.5, measured). `make doctor` checks all of this and the ports each stack needs; `make setup` and `make up` run the same checks first and stop before starting anything. It stops a start if a secret is missing from gen9-langfuse/.env (Langfuse's own compose file would fall back to a published default). On a running install it also checks Temporal's certificate, and warns of a deletion still running after a day (a step that keeps failing).

## First-time setup

Each stack generates its own secrets; Keycloak, Postgres and Langfuse also write the settings the apps need. One command does it for every stack and never overwrites anything:

```bash
make setup     # generates what is missing, asks for what it can't generate
make up
```

`make setup` asks for three things. Two are provider keys, which it saves in `gen9-models/.env` (readable only by you): OpenRouter's, for `chat`, vision and embeddings, and OpenAI's, for speech and images. gen9-agent reaches every model through the router (gen9-models) by alias and holds no provider key; other providers' keys go there too (gen9-models/README.md). The third is Langfuse's first user, the account you sign in to Langfuse with. Without a terminal it asks nothing: pass them as `make setup OPENROUTER_API_KEY=… OPENAI_API_KEY=… LANGFUSE_EMAIL=you@example.com LANGFUSE_NAME="Your Name"` (or export them), or put the keys in `gen9-models/.env` yourself; it says which are missing. Langfuse's project keys go to `gen9-agent/langfuse.local.env`, which gen9-agent reads before its `.env`.

`make setup` is safe to rerun: it keeps every `.env`, and if a settings file one stack wrote for another is gone (say `gen9-agent/keycloak.local.env`), it rebuilds it from that stack's `.env` (`init-env.sh --from-env`) without generating new secrets, so nothing is lost. `gen9-agent/langfuse.local.env` is the exception, since it overrides keys you may have put in `gen9-agent/.env`: rebuild it with `gen9-langfuse/init-env.sh --from-env --agent-env-file ../gen9-agent/langfuse.local.env`.

Then open http://localhost:14000 and sign in as `ada@gen9.test` (admin) or `alan@gen9.test`, with passwords from `grep ^GEN9_SEED_ gen9-keycloak/.env` (admins need a second step: `make admin-code` prints Ada's authenticator code), or create an account (emails arrive in Mailpit at http://localhost:15002).

## Everyday commands

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

Each stack labels the services you open (`gen9.name`, `gen9.url` in its Compose file), from the same settings the stack itself uses, so the list stays right when you change a port. `make up` also notes when a stack it starts calls another that isn't running, and restarts a container failing its health check, unhealthy or on its way there (the agent's API after its database was wiped, say), before waiting for it: Compose's `--wait` fails at once on an unhealthy container it doesn't replace (docker/compose#9092), while a restarted one gets its start period again.

What each service logs, and the records kept on purpose (sign-in events, the audit record, traces), who can read, change or erase them, for how long, and how to send the logs to a separate system: [docs/logging.md](logging.md).

Each stack's own setup (its `init-env.sh` options, such as other ports) is in its README; `make setup` runs them with the defaults. `make up` refuses to start a stack whose files are missing and says which `make setup` writes them.

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
