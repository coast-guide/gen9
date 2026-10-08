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
which is as much as root there. They are for operating, debugging and investigating: nothing
in Gen9 reads them, and none leaves the host unless the operator sends them elsewhere
([below](#sending-the-logs-elsewhere)).

`make logs` prints them as text: colours dropped, any other terminal escape shown as `^[` and other
control characters as `?`, so nothing in a log acts on your terminal (a username typed at a sign-in
can carry one). `docker logs` prints them as they are.

The containers' clock is UTC. gen9-agent's and Keycloak's lines start with the time in UTC with
its zone (`2026-10-01T00:46:24.364Z`); where another service's format prints no zone, the time is
UTC.

| Stack, service | What it logs | Format |
| --- | --- | --- |
| gen9-agent `api` | Each request (client address, method, path, status), its query values masked but counts and cursors (`GET /v1/search?q=…&mode=hybrid&limit=5`: what was searched, a file's name, an email an admin looked up stay out); a connector server whose TLS failed, with why; each audit record, as JSON (`audit.py`); warnings and errors | uvicorn's, after the time: `2026-10-01T01:00:05.956Z INFO:     172.24.0.1:65464 - "GET /v1/me HTTP/1.1" 200 OK`, `… INFO:     audit {"actor":…,"action":"thread.delete","outcome":"success",…}` |
| gen9-agent `worker` | Runs (attempts, waiting, done), deletions, Schedules, search indexing, notices; a service that doesn't answer as one line a try (`transient.py`); a connector server whose TLS failed, with why | `2026-10-01T01:00:05.645Z INFO gen9_agent.runs.activities: …` |
| gen9-ui `prod` | Back-channel logouts, server errors | Plain text, no time |
| gen9-keycloak `keycloak` | Sign-in events, successes and failures (Keycloak's `jboss-logging` listener at INFO: type, client, user id, IP address, the username or the email tried), and every change made through its Admin API (who, from where, the resource, not what was sent); warnings | `2026-10-01T00:57:52.234Z WARN  [org.keycloak.events] (executor-thread-6) …` (`KC_LOG_CONSOLE_FORMAT`) |
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
| A chat's environment's egress sidecar | Its policy as applied; a lookup the policy denies, with the host (`[dns] denied by policy`); a request the credential proxy refuses | JSON, `"ts":"2026-10-01T01:08:45.278Z"`, with the sandbox's id |

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

## Who can change or erase them

ASVS 5.0 16.4.2 asks that logs be "protected from unauthorized access and cannot be modified". Who
can read each is in the tables above. Who can change or erase each, from the running stacks and
the services' sources (docs/plans/manual-e2e.md, P6-C6):

| Log | Who can change or erase it |
| --- | --- |
| Container logs | Root on the host, and anyone in its `docker` group, which is as much: removing a container removes its log. gen9-sandbox's server holds Docker's socket to start the sandboxes, so whoever takes it over can too; no other container can reach them. Nothing records it. The oldest lines go once a container has 30 MB |
| Keycloak's sign-in and admin events | Keycloak's admins with `manage-events` (the master realm's admin): *Clear events* and *Clear admin events* delete them all and record nothing (Keycloak 26.7.5, `RealmAdminResource`); a shorter expiry is an admin event, which they can clear too. gen9-agent's service account holds `view-events` and no `manage-events` (its roles, read live: `manage-users`, `query-groups`, `query-users`, `view-events`, `view-users`), so a break-in through Gen9 can read them but not clear them. Deleting a person leaves their events to expire |
| Gen9's audit record | Nobody through Gen9: the API's and the worker's database role may only add and read rows, and can't lift the trigger (`e2e/audit.mjs`, eleven tries refused). The database's owner role, which only the migration job holds, and Postgres's superuser can. Nothing records it |
| The router's spend log | gen9-agent's worker, one person's rows at a time (`POST /users/{sub}/erase` of gen9-models' admin API, as deleting an account does); the router's master key and its database's owner, anything |
| Traces | gen9-agent, with the Langfuse project's keys, a chat's or a person's (as deleting them does); Langfuse's project owners and admins; its databases |
| Workflow histories | gen9-agent's worker, a chat's or a person's executions (as deleting them does); Gen9's admins in Temporal's web UI (`gen9:admin`, the namespace's admin); the namespace's retention, 72 hours after a workflow closes |
| Emails | Whoever has `MAILPIT_UI_PASSWORD` |

So someone who breaks in can erase what they reach: every container log as root on the host,
Keycloak's events as its admin, the audit record as the database's owner. A copy already sent to
another system is out of their reach: the next section.

## Sending the logs elsewhere

ASVS 5.0 16.4.3: logs "securely transmitted to a logically separate system", so that "if the
application is breached, the logs are not compromised". Gen9 doesn't run that system: which one
(Loki, Elasticsearch, a SIEM, syslog) is the operator's. What to send, and how:

- **What: every container's log.** It carries what an investigation needs: each audit record
  (gen9-agent's API writes it as a line, `audit {…}` in JSON, before the row; its worker, the
  accounts its sweep finds deleted in Keycloak), every sign-in and
  sign-in failure and every change made through Keycloak's Admin API (its `jboss-logging`
  listener, successes at INFO), refused keys and budgets at the router, an environment's denied
  lookups, the services' errors.
- **How: a collector that reads them through Docker's API**, run by the operator next to Gen9,
  not in one of its stacks: it needs Docker's socket, which is root on the host. Gen9's containers
  keep Docker's `local` driver, so `make logs` and `docker logs` work as before and nothing in
  Gen9 changes; containers started later, each chat's environment among them, are picked up as
  they start. Tested with Grafana Alloy v1.20.1 on the running stacks:
  [`gen9-agent/explore/logging/ship/`](../gen9-agent/explore/logging/ship/) has the
  configuration (`config.alloy`), and [its notes](../gen9-agent/explore/logging/NOTES.md) what it
  showed. Point `LOGS_URL` at your system, with its certificate authority and credentials. In
  that test:
  - every container that logs arrived, labelled with its container and stack, the same lines as
    `docker logs` (the audit lines, Keycloak's events);
  - nothing was lost or repeated while Alloy was stopped for 30 s or the receiving system was down
    for 60 s: Alloy keeps how far it has read, and the local logs hold what it hasn't sent yet
    (30 MB a container: a longer outage on a busy one loses its oldest lines);
  - a one-shot job's log arrived too, thanks to the configuration's `status` filter (Docker
    otherwise lists running containers only);
  - a server whose certificate isn't from the configured authority got nothing, and Alloy's log
    said nothing of it: watch its `loki_write_sent_entries_total` to know sending works.
- **Or Docker's own logging drivers**, `syslog` over `tcp+tls://` (`syslog-tls-ca-cert`),
  `fluentd`, `gelf` and others: set in each Compose file's `x-logging` and in gen9-sandbox's
  `launch.py` for the environments. Docker keeps a local copy for `docker logs` ("dual logging").
  More to change, and a driver whose server is down holds up the containers' output unless
  `mode: non-blocking`, which drops lines when its buffer fills.

## One record, one line

gen9-agent's API and worker write every control character of a message and its values as its
escape (`\n`, `\x1b`), so a value from outside (a connector server's refusal, a provider's error)
can't add a line of its own or reach the terminal (`log_safety.py`; ASVS 16.4.1). Their tracebacks
keep their lines. Keycloak keeps a value's line breaks out of its event lines (a username typed
with one shows a space) but passes other control characters, which `make logs` shows as text.
The other services write their own lines as they do.

## Personal data in the logs

What a person is or wrote, in a full `make e2e`'s container logs (docs/plans/manual-e2e.md,
P6-C2; the audit lines and Keycloak's sign-ins since P6-C6), and why each is there:

- **Keycloak: a sign-in, failed or not, names the account (its username, which is the person's
  email, or the email tried) and the address it came from.** Needed: logging sign-ins and their
  failures is how an attack on an account is seen (ASVS 16.3.1); its database copy goes after 30
  days.
- **gen9-agent's API: each audit record** (and its worker, the sweep's), which names people by
  their `sub` alone, and what they named (a connector, a secret and its host), never a value.
- **SearXNG: a search an engine refused, with its address**, which holds the agent's query. Kept:
  SearXNG has no setting to leave it out, and the same query went to that engine.
- Nothing else. The API and the sandbox server mask query values (searches, file names, emails
  an admin looked up); gen9-postgres logs slow statements without their values; the worker, the web
  app and the other services log none of it.

## Alerts: what reaches a person or the operator

OWASP A09:2025 asks for alerting with thresholds and without alert fatigue. What Gen9 sends:

- **A person, by email, when how they sign in changes:** a password set (at sign-up, a change or a
  reset), an authenticator app, a passkey or recovery codes added or removed. NIST SP 800-63B-4:
  "When an authenticator is added, the CSP SHALL notify the subscriber", account recovery too,
  each with "clear instructions … in case the recipient repudiates the event". Keycloak's `email`
  event listener (`configure.sh` turns it on), for `UPDATE_CREDENTIAL` and `REMOVE_CREDENTIAL`
  only: Keycloak also records each change under an older name, which would mail it twice. The
  email says what changed, when in UTC, from which address, and what to do if it wasn't them.
- **Not failed sign-ins:** anyone who knows an address could fill its inbox, and the lockout after
  5 tries answers guessing (the person sees it on the sign-in page, an admin on *Users*).
- **A person, by email, about their runs:** a background run done or waiting for them, as they
  chose in *Settings*, *Notifications*.
- **Not sent, and why:** someone made an admin (recorded in the audit log; one with no second
  step sets one up at their next sign-in, which emails them as above); the shared model key near
  its daily budget (the router refuses calls past it, and its admin UI shows the spend: an
  operator who wants a warning sets the router's alerting); a chat's environment removed for
  its disk (logged by gen9-sandbox; the chat gets a new one at its next command).
