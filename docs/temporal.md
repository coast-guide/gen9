# Temporal in Gen9

Temporal is Gen9's durable backbone: anything that must finish, wait, retry or happen later runs as
a Temporal Workflow. We run Temporal Server v1.32.0 and the Python SDK temporalio 1.33.0, both MIT.
This page covers three things, each backed by Temporal's own documentation:
- where Gen9 uses Temporal;
- the rules that keep that use sound;
- what Gen9 deliberately doesn't use yet.

Elsewhere: the decisions and their dates are in `docs/plans/harness.md` (Decision Log); what the
probes showed is in `gen9-agent/explore/harness/NOTES.md`. Researched from:
- the docs repository (commit of 2026-09-18);
- the SDK source at tag 1.33.0;
- the server source at v1.32.0.

## What goes through Temporal, and what doesn't

The split:
- **Temporal owns execution:** which worker runs what, retries, timeouts, waits, cancellation and
  schedules.
- **Postgres (`gen9-postgres`) owns product data:** threads, runs as people see them, the run event
  log, LangGraph checkpoints and memories.

The model's tokens never pass through Temporal. The agent's Activity appends them to the run's
event log, which clients follow over SSE. Two reasons:
- **History limits.** A workflow history holds at most 51,200 events or 50 MB. A payload is at most
  2 MB. A run takes at most 10 in-flight and 2,000 total Updates
  (server `common/dynamicconfig/constants.go`, [limits](https://docs.temporal.io/workflow-execution/limits)).
- **Workflow Streams (public preview) cost history.** Its publishers send batched Signals every
  100 ms to 2 s, and each subscriber poll is an Update
  ([Workflow Streams](https://docs.temporal.io/workflow-streams)).

## Where Gen9 uses Temporal

| What | How, in Temporal | Workflow ID | When |
| --- | --- | --- | --- |
| A run (one turn of a chat) | `RunWorkflow`. The agent turn is one long Activity that heartbeats ([Long-Running Activity](https://docs.temporal.io/design-patterns/long-running-activity)). A retry resumes from the LangGraph Postgres checkpoint (same `metadata.run_id`). A successful run is then made searchable by the `index_run` Activity on `gen9-system`, retried while the model router is down; its failure never fails the run. | `run-<run id>` | Runs on Temporal; indexing with search |
| Stop | Cancelling the Workflow. The cancel reaches the Activity with its next heartbeat, and the Activity reports it as cancelled. | | Runs on Temporal |
| Steps after an answer | Search indexing is a separate Activity after the turn (`index_run`), retried on its own; its failure doesn't fail the answer. The chat's title (its first question) and a background run's email are set inside the turn's Activity once it has succeeded, the email best effort (tried again for a moment without the database, then given up; `notices.py`). What Gen9 remembers is its own entity workflow (below) | | Runs on Temporal, then Memory |
| Approvals and questions mid-task | The turn ends at a LangGraph interrupt, and `agent_turn` returns `waiting` with the interrupts' ids. The API checks each answer and stores it in Postgres (`run_inputs`, first answer wins), commits, then sends the `answered` Signal with IDs only. Once every request has one, the next turn resumes with the answers ([Approval](https://docs.temporal.io/design-patterns/approval)). A durable timeout (7 days by default, `RUN_WAIT_S`) ends the run as `expired`. | same run | Human in the loop: questions and approvals built |
| Retry after a failure | When the turn's retries run out (the provider down or out of credits) or the person is over budget, the `park_run` Activity records a request of kind `retry` and the run waits. The person's Retry is stored, then signalled, and the turn runs again from its checkpoint ([Resumable Activity](https://docs.temporal.io/design-patterns/resumable-activity)). A durable timeout ends it as `error`. | same run | Human in the loop: built |
| Scheduled tasks | Recurring tasks are [Schedules](https://docs.temporal.io/schedule) of `TaskFiringWorkflow`: overlap Skip, pause, time zone, a fixed second per task. One-off tasks start the same workflow with Start Delay ([Delayed Start](https://docs.temporal.io/design-patterns/delayed-start)), and Run now starts it at once. Each firing's Activity makes a chat and its run, then runs the chat's `RunWorkflow` as a child at priority 3 (the person as fairness key), abandoned if the firing ends | schedule `task-<id>` | Milestone 5, scheduled tasks |
| Deleting a chat | `DeleteThreadWorkflow`, in order: stop the active run and wait for it; remove its environment (behind the patch `remove-environments`); erase its Langfuse traces; delete its Temporal executions; delete its checkpoints and row. Then erase traces again after durable timers of 1 and 10 minutes, for traces ingested late. The API waits on its `deleted` Update ([early return](https://docs.temporal.io/design-patterns/early-return)). | `delete-thread-<id>` | Deletion on Temporal |
| Deleting an account | `DeleteAccountWorkflow`, which runs today's retry-safe order as Activities, each retried until it succeeds with no time limit (`make doctor` warns of one running over a day): sessions (the Keycloak user disabled and signed out), then Langfuse, then the connectors' tokens revoked at their servers (RFC 7009, best effort), its environments removed and its scheduled tasks' Schedules, then app data, the model router's records (gen9-models' admin API), executions, then the Keycloak user; traces and router records again after 1 and 10 minutes. Steps added later run behind patches: `revoke-connector-tokens`, `remove-environments`, `remove-tasks` and `erase-model-usage`. | `delete-account-<sub>` | Deletion on Temporal |
| Users deleted in Keycloak | A Schedule every 15 minutes (overlap Skip), instead of a loop inside every API process. It starts a `DeleteAccountWorkflow` per user as a child it doesn't wait for (`ParentClosePolicy.ABANDON`, behind the patch `sweep-starts-deletions`); those run the late trace passes too (`late-erasures-always`) | schedule `sweep-deleted-users` | Deletion on Temporal |
| Plugin sources | `SyncPluginSourceWorkflow`: one Activity fetches a source's marketplace with `git`, hardened, loads each plugin and keeps what it brings in Postgres; a source it can't fetch is marked failed and the Activity returns, so only an unexpected error is retried (3 attempts). Started when an admin adds a source or asks "Sync now" (`sync-plugin-source-<id>`, use-existing), and for every source by `SyncPluginSourcesWorkflow`, daily (`PLUGIN_SOURCES_SYNC_S`) | schedule `sync-plugin-sources` | Milestone 4, plugins |
| Connector directory | `SyncDirectoryWorkflow`: one Activity pages through the MCP registry (`updated_since`, `cursor`) and saves each page with the pass's cursor in Postgres, so a retry, or the next run, resumes where it stopped (the registry gives no uptime guarantees; a first pass takes hours). Hourly, as the Registry asks of its readers (overlap Skip) | schedule `sync-directory` | Milestone 2, the directory |
| Search: re-embedding and backfill | `ReindexSearchWorkflow`: builds the current `embed` model's index, then re-embeds 100 runs per Activity by a cursor (heartbeating), continuing-as-new when suggested, then drops old indexes. A Schedule every 15 minutes (overlap Skip) | schedule `reindex-search` | Search in gen9-postgres |
| A chat's environment | `EnvironmentWorkflow`, one per chat that has one: its `acquire` Update (Update-with-Start, from the run's first command or file) creates the sandbox once and renews it while used; after `SANDBOX_IDLE_S` unused, or on its `end` Signal (the chat or the account deleted, or the sandbox stopped under a command), it removes it, so a crash never leaves a container running. Its input comes in `@workflow.init`: the Update can run before `run` begins. Deleting a chat or an account removes environments too, behind the patch `remove-environments`: a chat's deletion ends its workflow, waits up to 45 s for it to remove its sandbox, then removes any the chat still has (both removing the same sandbox at once made Docker refuse one). Each `acquire` of a running sandbox also checks that its egress still holds the person's secrets and rules, which egress keeps in memory and loses when it restarts, and rewrites them if not (behind the patch `check-secrets`). When a person's environment secrets change, `RefreshEnvironmentsWorkflow` rewrites their running environments (one per person, a newer change replacing one still running) | `environment-<thread>`, `refresh-environments-<sub>` | Milestone 3 |
| A task's API trigger | `POST /v1/tasks/{id}/fire`, with the task's own token, starts a `TaskFiringWorkflow` as *Run now* does, once its hourly limits (checked in Postgres) allow it. Each call fires once: a caller's retry is a second firing, so none is deduplicated | `task-<task id>-now-<random>` | Milestone 5, scheduled tasks |
| Background tasks | The agent's `start_async_task` makes a chat of its own and a run in it: a `RunWorkflow` of its own, at background priority. When it ends, a child `TellChatWorkflow` (parent-close policy `ABANDON`) tells the chat that started it, waiting while that chat is busy (`background.py`) | `run-<run id>`, `tell-chat-<run id>` | Milestone 5, background tasks |
| Gen9 as an MCP server, A2A, AG-UI | An `ask`, a message or an AG-UI run starts a run as the web app does: a `RunWorkflow`. The MCP task and the A2A task follow it | `run-<run id>` | Milestone 6 |
| Stopping every agent | `make stop-agents` (`gen9-agent-stop`): every run not over is cancelled, as Stop does, and waited for; every task's Schedule is paused with a note; then the worker stops. `make resume-agents` unpauses only the Schedules that note names. An admin disabling a person cancels that person's runs the same way (`stop_runs_of`) | | manual-e2e.md, P5-C10 |


### Running a Schedule now

Gen9's own Schedules (`sweep-deleted-users`, `reindex-search`, `sync-directory`,
`sync-plugin-sources`) run on their own. To run one now, as an admin: Temporal's web UI
(http://localhost:18000, which lets in only Gen9 admins), *Schedules*, the Schedule, then
*Trigger*; or from gen9-temporal's CLI:

```bash
(cd gen9-temporal && docker compose run --rm cli temporal schedule trigger --schedule-id reindex-search)
```

A run already under way is left alone (overlap Skip). Gen9's API has no route for this:
Temporal already offers it to the same admins (gen9-learn plan, Decision Log, "run a Schedule now").

## Rules

Each rule is what Temporal's documentation says to do, applied to Gen9.

1. **Workflow code only decides; Activities do the I/O.** Workflow modules have no side effects at
   import, because the Python sandbox re-imports them
   ([sandbox](https://docs.temporal.io/develop/python/best-practices/python-sdk-sandbox)).
2. **Pass IDs, not content.**
   - This covers inputs, results and Update arguments: a run id, a thread id, a decision.
   - The conversation stays in Postgres
     ([limits](https://docs.temporal.io/workflow-execution/limits)).
3. **Activities are idempotent, because they run at least once.**
   - Events carry per-run sequence numbers.
   - LangGraph re-enters a run by its `metadata.run_id`.
   - Deleting something already gone succeeds
     ([idempotence](https://docs.temporal.io/best-practices/error-handling#idempotence)).
4. **Long Activities heartbeat.**
   - The agent turn heartbeats every half second, with a 3 s heartbeat timeout.
   - Why so short: a cancel arrives only with a heartbeat's reply, and the SDK holds heartbeats back
     to 0.8 × the timeout unless told otherwise: its worker sends them at most every 0.5 s
     (`max_heartbeat_throttle_interval`), so Stop reaches a turn within about half a second (it lost
     to fast answers at 2.4 s).
   - Its heartbeat carries the commands it has running in the chat's environment; a retry reads
     them from the last heartbeat (`heartbeat_details`) and stops them first, since a crashed
     worker's command runs on and the resumed turn would run it again.
   - Start-to-close is only a safety net.
   - Model calls never run as Local Activities
     ([Long-Running Activity](https://docs.temporal.io/design-patterns/long-running-activity)).
5. **Classify failures.**
   - Non-retryable: bad input, a missing permission, a rejected API key, a content-policy refusal,
     a user over their model budget (the router's 429 `budget_exceeded`, told apart from a rate
     limit by its type).
   - Retried with backoff: rate limits, timeouts and 5xx errors
     ([error handling](https://docs.temporal.io/best-practices/error-handling)).
   - Model calls are retried first inside the router (gen9-models: retries, then fallbacks), and
     the client doesn't retry. Temporal retries the turn only after the router gives up, so
     retries don't multiply.
6. **Child workflows only when they need their own ID, lifecycle or history.** Examples: a
   scheduled task and its run, subagents. "There is no reason to use Child Workflows just for code
   organization" ([child workflows](https://docs.temporal.io/child-workflows)).
7. **Long-lived workflows (per-user memory, sandboxes):**
   - They continue-as-new when `workflow.info().is_continue_as_new_suggested()`.
   - They do it from the main method, after `workflow.all_handlers_finished()`.
   - They carry the Update IDs they processed across it.
   - They complete when idle
     ([entity workflow](https://docs.temporal.io/design-patterns/entity-workflow)).
8. **Decisions are checked before they reach a workflow.**
   - Where Postgres holds the truth (a person's answer to a waiting run), the API checks and stores
     it first, then sends a Signal. A Signal is recorded at once, even while no worker runs.
   - An Update needs a worker to accept it. One whose call timed out was still applied once a
     worker came back, so a timeout doesn't mean it failed (explore/hitl/NOTES.md). An API that
     rolled back on that timeout would leave the workflow believing in an answer Postgres
     doesn't have.
   - Updates, with validators, are for decisions only the workflow can judge, so that an invalid
     one never enters history ([handling messages](https://docs.temporal.io/handling-messages)).
9. **Schedules, not cron workflows. Start Delay for one-off tasks**
   ([cron job](https://docs.temporal.io/cron-job), [schedule](https://docs.temporal.io/schedule)).
10. **A task queue per workload.**
    - `gen9-agent` runs agent turns: long work, few concurrent slots.
    - `gen9-system` runs deletions, sweeps and short steps.
    - Queue names are constants shared by clients and workers, because a mistyped name silently
      makes a new queue ([worker best practices](https://docs.temporal.io/best-practices/worker)).
11. **Priority and fairness within a queue, rather than one queue per user.**
    - Priority 1 for someone waiting in the chat, 3 for scheduled and background runs, 5 for
      maintenance.
    - The fairness key is the user's `sub`, so one person's many runs can't starve everyone else.
    - Both proven through the real path by `e2e/fairness.mjs`, with one agent slot.
    - Self-hosted needs `matching.enableFairness`
      ([priority and fairness](https://docs.temporal.io/develop/task-queue-priority-fairness),
      [multi-tenant patterns](https://docs.temporal.io/best-practices/multi-tenant-patterns)).
    - Both hold only within one partition of a task queue, and a queue has four by default, each
      task on a random one. The `gen9-agent` queue has one partition
      (`matching.numTaskqueueReadPartitions` and `WritePartitions`), which one host's turns fit
      in. With four, a person's run once waited behind all four of another's that were still
      queued.
12. **Every change to workflow code is replay-safe.**
    - Changed logic goes behind `workflow.patched()`.
    - A replay test in CI runs recorded histories against the new code: one of every workflow
      kind Gen9 has run on the stacks (`gen9-agent/tests/histories/`, recorded with its
      `record.py`), and before a release, every history Temporal still holds (a replay of
      434 found none failing; manual-e2e.md, P3-C8).
    - This matters because an approval can wait for days
      ([patching](https://docs.temporal.io/patching),
      [testing](https://docs.temporal.io/develop/python/best-practices/testing-suite)).
13. **Temporal gets its own Postgres, which nothing else uses.** This is also how Gen9's stacks
    stay decoupled.
14. **The web app never polls workflows with Queries.** It follows Postgres (events over SSE).
    Queries serve operators and tests.

## Conventions

- **Namespace `gen9`.** Closed executions are kept 3 days; the minimum is 1 day
  ([retention](https://docs.temporal.io/temporal-service/temporal-server#retention-period)).
  Deleting a chat or an account deletes its executions at once.
- **Search attributes (Keyword):**
  - `Gen9User`: the user's `sub`.
  - `Gen9Thread`.
  - `Gen9Kind`: run, delete-thread, and so on.
  - `Gen9RunState`: `waiting` while a run waits for the person (an answer, a decision, a Retry),
    `running` once it goes on (set by `RunWorkflow`).

  Postgres visibility allows 10 Keyword attributes per namespace
  ([limits](https://docs.temporal.io/search-attribute#custom-search-attribute-limits)).
- **Timeouts.** Agent turn: a 3 s heartbeat timeout and a 60 min start-to-close. Approvals wait 7
  days unless configured otherwise.
- **History shards: 512.** That is Temporal's recommendation for small production clusters, and the
  number can't change later
  ([Scaling Temporal](https://temporal.io/blog/scaling-temporal-the-basics),
  [configuration](https://docs.temporal.io/references/configuration#numhistoryshards)).
- **Upgrades go one minor version at a time**
  ([upgrade server](https://docs.temporal.io/self-hosted-guide/upgrade-server)). Server v1.33 removes
  the legacy Worker Versioning APIs and the legacy query converter; Gen9 uses neither.

## Security

Temporal's ports are published on 127.0.0.1 only; other stacks reach its frontend over the
`gen9-temporal` network. Four layers, each verified live (`e2e/temporal.mjs`, CI):

**Frontend authorization.** The server checks a Keycloak JWT on every call
(Temporal's default authorizer and JWT claim mapper, set up from the image's environment variables:
`TEMPORAL_AUTH_AUTHORIZER`, `TEMPORAL_AUTH_CLAIM_MAPPER`, `TEMPORAL_JWT_KEY_SOURCE1`; no custom
build). Roles come from the `permissions` claim as `<namespace>:<role>` (worker < reader < writer <
admin), granted as roles of Keycloak client `temporal`:
- gen9-agent's service account holds `gen9:write`. Its client connects with the token as
  `api_key` (`tls=False` inside Docker), checks it every 15 s and renews it before it expires,
  judged by wall time; the SDK sends a new key on the next call (`gen9_agent/temporal.py`). With
  the monotonic clock instead, a host that slept (Docker's VM paused) kept an expired token for
  minutes after waking, and Temporal refused it ("Token is expired").
- Gen9 admins hold `gen9:admin` and `temporal-system:read` (the UI reads the cluster; the system
  scope is named `temporal-system`).
- Nobody else holds anything.

**Internode mTLS.** Temporal's system workers (Schedules, batch jobs) call an
internal frontend (`USE_INTERNAL_FRONTEND`), which grants every caller `System: admin` with no
token (server `common/authorization/claim_mapper.go`, `internalClaimMapper`). The server container
also sits on `gen9-temporal` and `gen9-keycloak` (for Keycloak's keys). So other stacks' containers
could otherwise reach the internal frontend, history, matching and membership ports directly;
probed live, they could. Temporal secures those with internode TLS. Gen9 requires a client
certificate from a private CA on every one of them (`TEMPORAL_TLS_REQUIRE_CLIENT_AUTH`, and
`system.enableRingpopTLS`, which is off by default). It also turns off the internal frontend's HTTP
API (`INTERNAL_FRONTEND_HTTP_PORT=0`): that server takes its TLS settings from the frontend's
section, so it served admin rights in plaintext (probed: `GET /api/v1/namespaces` answered 200 with
no credentials). `gen9-temporal/init-tls.sh` makes the CA and certificate; only the stack's own
`namespace` job and `cli` tool hold it.

**Web UI sign-in.** The UI signs in through Keycloak OIDC (client `temporal-ui`)
and sends the user's access token with every call, so the frontend's roles apply: admins see
everything. Anyone else is turned away at Keycloak, in Gen9's words (“Temporal is for admins”, “Temporal
is for Gen9 admins…”, and *Back to Gen9*), even with a Keycloak session already: temporal-ui signs in with a flow of its own,
`gen9-temporal-ui` (gen9-keycloak's `config/configure.sh`). Before, they got in with no role and
the UI sent them back to its sign-in page, over and over.

**Payload encryption.** gen9-agent encrypts every payload it sends to Temporal
with AES-256-GCM (`gen9-agent/src/gen9_agent/codec.py`, adapted from Temporal's Python encryption
sample):
- workflow and Activity inputs and results, Update arguments;
- failure messages and stack traces (`DefaultFailureConverterWithEncodedAttributes`; Temporal
  stores those in the clear by default). Gen9's subclass also stops a chain of causes where it
  loops: OpenSandbox's SDK makes each of its errors its own cause, and Temporal's converter then
  fails with RecursionError, losing the message (temporalio/sdk-python#697).

Keys:
- They come from `TEMPORAL_PAYLOAD_KEYS` in `gen9-agent/.env`: `id:base64` pairs, where the first
  encrypts and all decrypt, so keys rotate.
- `make setup` generates one; Temporal never sees it.
- Payloads written before encryption pass through, so older histories still replay.

Only search attributes stay readable, since Temporal indexes them; they hold ids.

**A codec endpoint for the UI.** gen9-agent serves Temporal's codec protocol at
`/v1/temporal/codec/{decode,encode}` (`api/temporal_codec.py`,
[codec server](https://docs.temporal.io/codec-server)), for tokens from client `temporal-ui` with
audience `temporal` and `gen9:admin`; CORS allows only the UI's origin. The UI passes the user's
token only to an `https://` codec endpoint (temporal-ui `src/lib/services/data-encoder.ts`,
`validateHttps`). So a plain-http local install shows ciphertext, and a deployment with TLS in front
of gen9-agent sets `GEN9_TEMPORAL_CODEC_URL=https://…`. e2e/temporal.mjs exercises both.

Not yet:
- **TLS on the frontend**, so tokens don't cross Docker networks in plaintext. It needs gen9-agent
  and the UI to trust the stack's CA, and the host port to serve TLS. Revisit with the deployment
  milestone (TLS in front of every service).

## Not used yet, and when we'd revisit

| Feature | Status | Why not now | Revisit when |
| --- | --- | --- | --- |
| Deep Agents plugin ([docs](https://docs.temporal.io/develop/python/integrations/deepagents)) | Pre-release. 1.33.0 still changed how it dedups calls (behind a patch). | It runs the agent loop in the Workflow, where a durable checkpointer "is not replay-safe", and conversation state moves into history. Every I/O tool must be wrapped. | It is GA, and Gen9's conversation can stay in Postgres |
| LangGraph plugin ([docs](https://docs.temporal.io/develop/python/integrations/langgraph)) | Public Preview | It needs `InMemorySaver`, and "Stores are not supported", while Gen9's memory is a Store. | As above |
| Workflow Streams | Public Preview | See "What goes through Temporal" | Only ever for coarse status events |
| Worker Versioning ([docs](https://docs.temporal.io/worker-versioning)) | Recommended for production | Pinned workflows need old and new workers running side by side; one Compose worker gains little. Patching and replay tests instead. | Gen9 ships a Kubernetes deployment (Temporal Worker Controller) |
| Standalone Activities ([docs](https://docs.temporal.io/standalone-activity)) | GA in server 1.32, SDK 1.33 | Every job so far has several steps or belongs to a workflow. | A single-step background job started from the API |
| External Storage ([docs](https://docs.temporal.io/external-storage)) | Public Preview | Gen9 passes IDs. | Payloads grow |
| Nexus | Self-hosted docs call external Nexus calls experimental | Gen9 has one namespace. | Gen9 splits namespaces |
| Workflow Pause | Pre-release, behind a flag | Operators can already cancel or reset, or stop every agent (`make stop-agents`). | It is GA |
| Eager workflow start | Available | The starter and the worker must share a process; Gen9's API and worker are separate services. | Never, by design |
| Memory consolidation: a per-person [entity workflow](https://docs.temporal.io/design-patterns/entity-workflow), fed with Signal-with-Start after each run, debounced with an [updatable timer](https://docs.temporal.io/design-patterns/updatable-timer) | Decided against (docs/plans/harness.md, Decision Log) | Memory is written during the chat, and past chats are found by a search tool, as Claude now does | A person's memory outgrows what a chat can keep in order |

## Sources

- Temporal docs:
  - [Durable AI](https://docs.temporal.io/ai)
  - [design patterns](https://docs.temporal.io/design-patterns)
  - [best practices](https://docs.temporal.io/best-practices)
  - [self-hosted production checklist](https://docs.temporal.io/self-hosted-guide/production-checklist)
  - [limits](https://docs.temporal.io/workflow-execution/limits)
- Temporal Server [v1.32.0 release notes](https://github.com/temporalio/temporal/releases/tag/v1.32.0):
  Standalone Activities GA; legacy Worker Versioning removed in v1.33.
- Python SDK [1.33.0 release notes](https://github.com/temporalio/sdk-python/releases/tag/1.33.0).
- Temporal's [AI cookbook](https://github.com/temporalio/ai-cookbook): human-in-the-loop, durable MCP
  server, claim check.
