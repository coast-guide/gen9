# Logs: what Gen9 keeps, where, and for how long

The log inventory OWASP ASVS 5.0 16.1.1 asks for: at each layer, what is logged, in what format,
where it is kept, what it is for, who can read it, and for how long. Each entry was read from the
running stacks (docs/plans/manual-e2e.md, P6-C1). How big each store grows is in
[docs/operations.md, "Disk"](operations.md#disk).

Two kinds: what each container writes to its output (read with `make logs` or `docker logs`), and
records kept in the stacks' databases on purpose.

## Container logs

Every container of every stack, and each chat's environment (gen9-sandbox's `launch.py`), logs
through Docker's `local` driver: at most three files of 10 MB a container, compressed, the oldest
lines going first (`x-logging` in each Compose file). They go when the container is removed, as
`make up` does when its settings change. Anyone who can run `docker` on the host can read them,
which is as much as root there. They are for operating and debugging: nothing reads them
automatically, and none leaves the host.

`make logs` prints them as text: colours dropped, any other terminal escape shown as `^[` and other
control characters as `?`, so nothing in a log acts on your terminal (a username typed at a sign-in
can carry one). `docker logs` prints them as they are.

The containers' clock is UTC. gen9-agent's and Keycloak's lines start with the time in UTC with
its zone (`2026-10-01T00:46:24.364Z`); where another service's format prints no zone, the time is
UTC.

| Stack, service | What it logs | Format |
| --- | --- | --- |
| gen9-agent `api` | Each request (client address, method, path, status), its query values masked but counts and cursors (`GET /v1/search?q=…&mode=hybrid&limit=5`: what was searched, a file's name, an email an admin looked up stay out); a connector server whose TLS failed, with why; warnings and errors | uvicorn's, after the time: `2026-10-01T01:00:05.956Z INFO:     172.24.0.1:65464 - "GET /v1/me HTTP/1.1" 200 OK` |
| gen9-agent `worker` | Runs (attempts, waiting, done), deletions, Schedules, search indexing, notices; a service that doesn't answer as one line a try (`transient.py`); a connector server whose TLS failed, with why | `2026-10-01T01:00:05.645Z INFO gen9_agent.runs.activities: …` |
| gen9-ui `prod` | Back-channel logouts, server errors | Plain text, no time |
| gen9-keycloak `keycloak` | Sign-in events (Keycloak's `jboss-logging` listener: type, client, user id, IP address; failures with the email tried), warnings | `2026-10-01T00:57:52.234Z WARN  [org.keycloak.events] (executor-thread-6) …` (`KC_LOG_CONSOLE_FORMAT`) |
| gen9-keycloak `mailpit` | Its start and errors | logfmt, `time="2026/09/30 22:49:09" level=info msg=…` |
| gen9-temporal `temporal`, `ui` | The server's events and errors; the UI's requests | JSON, `"ts":"2026-09-30T23:34:20.611Z"` |
| gen9-models `litellm` | Warnings and errors only (`LITELLM_LOG=WARNING`): refused keys, budgets exceeded (with the person's `sub`) | Plain text |
| gen9-models `searxng` | Search engines' errors; a request an engine refused, with its address, which holds the query the agent searched for (SearXNG has no setting to leave it out) | `2026-09-30 22:36:14,895 WARNING:searx.engines…`, no zone |
| gen9-langfuse `langfuse-web`, `langfuse-worker` | Ingestion, trace deletions | `2026-09-30T23:30:50.264Z info …` |
| gen9-langfuse `clickhouse` | Warnings and errors; also its own files inside the container, three of 100 MB (`config.d/gen9-disk.xml`, gen9-langfuse's README, "Disk") | ClickHouse's text log |
| Every stack's `postgres` | Checkpoints, errors; gen9-postgres also every statement slower than 500 ms, without its values (`log_min_duration_statement`, `log_parameter_max_length=0`) | `2026-09-30 23:34:22.608 UTC [31] LOG: …` |
| gen9-ui `valkey` | Its start and saves | `6:M 30 Sep 2026 18:10:53.246 * …`, no zone |
| gen9-sandbox `opensandbox` | Its API's requests, their query values masked (a chat's file names stay out; `launch.py`) | uvicorn's, `2026-09-30 23:35:55+0000` |
| A chat's environment | What its commands print | As printed |

## Records kept on purpose

| Record | Where | What it holds | Who can read it | How long |
| --- | --- | --- | --- | --- |
| Sign-in events | gen9-keycloak's Postgres | 103 event types: sign-ins and their failures, sign-outs, password, authenticator and profile changes, consents, emails sent, with the user id, the app (client), the IP address and the time (`events/config`) | Keycloak's admins (its console and Admin API); each person their own, in Settings' *Download a copy* (`sign-ins.json`) | 30 days (`eventsExpiration`) |
| Keycloak's admin events | gen9-keycloak's Postgres | Every change made through Keycloak's Admin API, with what was sent (`adminEventsDetailsEnabled`); Gen9's own changes appear as its service account's | Keycloak's admins | 30 days (`adminEventsExpiration`, set by `configure.sh`) |
| Gen9's audit record | gen9-postgres, `audit_events` | Admin actions, people's security actions, access refused, and an answer sent again to a run's question (a replayed approval): when, where (the route), who (the person's `sub`), what, the outcome ([gen9-agent's README, "How auth works"](../gen9-agent/README.md#how-auth-works)). Never tokens, passwords, secret values or message text | Gen9's admins (*Audit log*, `GET /v1/admin/audit`); the database's superuser | Kept for good. Append-only: triggers refuse changes, deletions and truncation, and the services' own role can't lift them (`e2e/audit.mjs`) |
| The router's spend log | gen9-models' Postgres (`LiteLLM_SpendLogs`, daily totals) | Each model call: model, tokens, cost, status, the person's `sub`. No prompts or answers (none stored: 0 of 2,036 rows) | The router's admin API and UI (its master key) | Until the person's account is deleted, which erases their rows; the per-key daily totals stay |
| Traces | gen9-langfuse (ClickHouse, and MinIO for what they reference) | Each run's steps: the question, the model's answers, tools called and their results | Langfuse's users (its own sign-in) | Until the chat or the account is deleted: Gen9 erases their traces then |
| Langfuse's raw copies | gen9-langfuse's MinIO, `events/` | Each batch as it arrived, what the traces hold | MinIO's credentials | A day (rounded up to the next midnight UTC), by `minio-lifecycle`'s rule |
| Workflow histories | gen9-temporal's Postgres | Each workflow's steps; Gen9's inputs and outputs encrypted by its payload codec | Gen9's admins in Temporal's web UI (decrypted by gen9-agent's codec endpoint, over https only); anyone with its database sees ciphertext | 72 hours after a workflow closes (the namespace's retention) |
| Emails | gen9-keycloak's Mailpit | Every email sent: verification, password resets, Gen9's notices | Whoever has `MAILPIT_UI_PASSWORD` | The newest 5,000 |

## One record, one line

gen9-agent's API and worker write every control character of a message and its values as its
escape (`\n`, `\x1b`), so a value from outside (a connector server's refusal, a provider's error)
can't add a line of its own or reach the terminal (`log_safety.py`; ASVS 16.4.1). Their tracebacks
keep their lines. Keycloak keeps a value's line breaks out of its event lines (a username typed
with one shows a space) but passes other control characters, which `make logs` shows as text.
The other services write their own lines as they do.

## Personal data in the logs

What a person is or wrote, in a full `make e2e`'s container logs (docs/plans/manual-e2e.md,
P6-C2), and why each is there:

- **Keycloak: a failed sign-in names the account tried and the address it came from.** Needed:
  logging failed sign-ins is how an attack on an account is seen (ASVS 16.3.1); its database copy
  goes after 30 days.
- **SearXNG: a search an engine refused, with its address**, which holds the agent's query. Kept:
  SearXNG has no setting to leave it out, and the same query went to that engine.
- Nothing else. The API and the sandbox server mask query values (searches, file names, emails
  an admin looked up); gen9-postgres logs slow statements without their values; the worker, the web
  app and the other services log none of it.

## Known gaps

Found while taking this inventory; each is an item of docs/plans/manual-e2e.md, phase 6:

- An environment's lookup of a host its policy denies isn't logged: OpenSandbox's egress v1.1.7, the
  latest, doesn't say (P6-C7).
- No log leaves the host for a separate system (P6-C5, P6-C6; ASVS 16.4.3).
