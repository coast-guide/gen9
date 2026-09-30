# gen9-temporal

[Temporal](https://temporal.io) Server 1.32.0 (MIT), the durable execution service behind Gen9. It
covers anything that must finish, wait, retry or happen later: agent runs, approvals that wait for a
person, scheduled tasks, deleting a chat or an account. Where Gen9 uses it, and the rules that keep
that sound: [docs/temporal.md](../docs/temporal.md).

The stack is five services. It is adapted from Temporal's own Compose setup for Postgres
([samples-server](https://github.com/temporalio/samples-server/tree/main/compose)):

| Service | What |
| --- | --- |
| `postgres` | Postgres 16, used by Temporal only: databases `temporal` (workflow state) and `temporal_visibility` (the index for listing and searching workflows) |
| `schema` | One-shot job: creates or upgrades both schemas with `temporal-sql-tool` |
| `temporal` | The server (frontend, history, matching and internal worker in one process) |
| `namespace` | One-shot job: namespace `gen9`, its retention and its search attributes |
| `ui` | Temporal's web UI, behind Gen9's sign-in |
| `cli` | On demand only: the `temporal` CLI as this stack's operator (`docker compose run --rm cli temporal …`) |

**Requires:** Docker with Compose v2, bash, OpenSSL (or LibreSSL). The sign-in needs gen9-keycloak
set up first: `make setup` writes `keycloak.local.env`.

## Quick start

```bash
./init-env.sh                                            # generate .env (random passwords)
./init-tls.sh                                            # generate tls.local.env (internode certificate)
docker network inspect gen9-temporal >/dev/null 2>&1 || docker network create gen9-temporal   # once; make up does it
docker compose up -d --wait                              # start, wait until healthy
```

`make setup STACKS="keycloak temporal"` and `make up` do all of this.

Open the web UI at http://localhost:18000 and sign in as a Gen9 admin. From the host, workers reach
the frontend at `127.0.0.1:18001`, namespace `gen9`, with a Keycloak token (Security).

Another stack's containers join the `gen9-temporal` network and use `gen9-temporal:7233`. `make up`
creates the network; by hand: `docker network create gen9-temporal`.

> [!IMPORTANT]
> Two settings take effect only when the data volume is first created, so change them before the
> first start:
> - **Passwords.** `init-env.sh` refuses to regenerate them once the volume exists.
> - **The number of history shards (512).** It is fixed then.

## What gets created

**On the first start only** (`initdb/10-temporal-databases.sh`):
- Role `temporal`: login only, no superuser, `CREATEDB` or `CREATEROLE`.
- Databases `temporal` and `temporal_visibility`, both owned by that role, with access revoked from
  `PUBLIC`.
- Extension `btree_gin` in `temporal_visibility`. The visibility schema needs it, and creating it
  here means the schema job never needs more than the owner's rights.

**On every start**, and safe to repeat:
- `scripts/setup-schema.sh`. `setup-schema -v 0.0` records a version only on an empty database, and
  `update-schema` applies only the versions newer than the recorded one. A server upgrade therefore
  brings its schema along before the server starts.
- `scripts/setup-namespace.sh`:
  - creates namespace `gen9` or updates its retention (72 h);
  - adds the Keyword search attributes `Gen9User`, `Gen9Thread`, `Gen9Kind` and `Gen9RunState`.

**Server settings:**
- `NUM_HISTORY_SHARDS=512`, Temporal's recommendation for small production clusters. Idle, the
  server uses about 235 MiB and under 2% CPU.
- `dynamicconfig/gen9.yaml` turns on task queue fairness, so one person's runs can't hold back
  another's. Priority is on by default. Both hold within one partition of a task queue, so the
  agent queue has one (a queue has four by default, each task on a random one). It also keeps an
  activity's last failure while it retries up to 64 KB, not 4 KB, so the UI shows why a step keeps
  failing (a deletion waiting on Langfuse, say) instead of "Failure exceeds size limit.".
- The web UI doesn't fetch Temporal's news feed (`TEMPORAL_DISABLE_NEWS_FETCH`).
- Sign-in, tokens and internode mTLS: see Security.

## Files

| File | Purpose |
| --- | --- |
| `compose.yaml` | The services, images pinned as `tag@digest` |
| `initdb/` | First-start SQL: role, databases, extension |
| `scripts/setup-schema.sh` | Schema creation and upgrades (the `schema` job) |
| `scripts/setup-namespace.sh` | Namespace, retention, search attributes (the `namespace` job) |
| `scripts/internode.sh` | Runs the `temporal` CLI on the internal frontend with the internode certificate (`namespace`, `cli`) |
| `dynamicconfig/gen9.yaml` | Server dynamic configuration (re-read every 60 s, except `system.enableRingpopTLS`) |
| `init-env.sh` | Generates `.env` (passwords, ports). See `--help` |
| `init-tls.sh` | Generates `tls.local.env`: a private CA and the internode certificate. See `--help` |
| `.env`, `tls.local.env` | Generated, secret, gitignored |
| `keycloak.local.env` | The web UI's Keycloak client (`temporal-ui`), written by gen9-keycloak's setup; gitignored |

## Ports

| Port | Service | `.env` variable |
| --- | --- | --- |
| `18000` | Web UI | `GEN9_TEMPORAL_UI_PORT` |
| `18001` | Frontend (gRPC), for workers on the host (with a Keycloak token) | `GEN9_TEMPORAL_PORT` |

Postgres isn't published. Use `docker compose exec postgres psql -U postgres -d temporal`.

## Security

Both ports listen on 127.0.0.1 only. Behind them, three rules:

**Callers from other stacks need a Keycloak token.** The frontend (7233) checks a JWT on every call
with Temporal's default authorizer and claim mapper: signature against Keycloak's keys
(`TEMPORAL_JWT_KEY_SOURCE1`), then the roles in its `permissions` claim, as `<namespace>:<role>`.
gen9-keycloak grants them as roles of its client `temporal`:

| Who | Token from | `permissions` | Can |
| --- | --- | --- | --- |
| gen9-agent (API, worker) | its service account, client credentials | `gen9:write` | start, signal, update, cancel and delete workflows; poll task queues |
| Gen9 admins (group `admins`) | the web UI's sign-in (client `temporal-ui`) | `gen9:admin`, `temporal-system:read` | everything in `gen9`, and read the cluster (the UI needs it) |
| Anyone else | | none | nothing: a signed-in user who isn't an admin sees no workflow |

Without a token, the frontend answers `Request unauthorized`. gen9-agent refreshes its token before
it expires (`temporal.py`, `keep_token_fresh`).

**The stack's own parts use mTLS.** The server's system workers (Schedules, batch jobs) call an
*internal frontend* (7236) that takes no token and treats every caller as an admin. So every port
but the frontend's requires a certificate signed by this stack's private CA (`init-tls.sh`):
internal frontend, history, matching, the internal worker, and membership
(`TEMPORAL_TLS_REQUIRE_CLIENT_AUTH`, `system.enableRingpopTLS`). This matters because the server
also sits on `gen9-temporal` and `gen9-keycloak`, networks other stacks share. The internal
frontend's HTTP API is turned off (`INTERNAL_FRONTEND_HTTP_PORT=0`): its server takes TLS settings
from the frontend, not internode, so it would serve admin rights in plaintext. The `namespace` job
and the `cli` tool hold the certificate. No other stack does.

**Payloads are ciphertext.** gen9-agent encrypts workflow and Activity payloads and failure messages
(AES-256-GCM; the key is only in gen9-agent). Only search attributes (ids) are readable here.
Signed-in admins can have the web UI decode payloads through gen9-agent's codec endpoint
(`/v1/temporal/codec`), which checks their token (client `temporal-ui`, audience `temporal`,
`gen9:admin`). The UI sends that token only to an `https://` codec endpoint, so on this plain-http
local install it can't: payloads show as they are stored, a workflow's input as its encoding and
key id with the data in base64 and its result as a base64 string (UI 2.54.1, seen; a workflow's input showed as `null`). With TLS in front of
gen9-agent, set
`GEN9_TEMPORAL_CODEC_URL=https://…/v1/temporal/codec` in `.env` (e2e/temporal.mjs proves that path
through a throwaway https proxy).

The internode certificate lasts 825 days; `make doctor` warns 30 days before. To renew:
`./init-tls.sh --force`, then `docker compose up -d --wait`.

Not yet: TLS on the frontend itself, for tokens crossing the Docker networks (docs/temporal.md,
"Security").

## Verify

```bash
docker compose run --rm cli sh -c 'temporal operator namespace describe -n gen9 | grep -i retention; temporal operator search-attribute list -n gen9 | grep Gen9'
docker run --rm --network gen9-temporal temporalio/admin-tools:1.32.0 \
  temporal workflow list -n gen9 --address gen9-temporal:7233   # Request unauthorized: no token
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:18000/   # 200, the sign-in page
node ../e2e/temporal.mjs   # sign-in, roles, the codec endpoint, in Chrome
```

## Operate

| Task | Command |
| --- | --- |
| Status | `docker compose ps -a` (`schema` and `namespace` show `Exited (0)`: they ran) |
| The CLI | `docker compose run --rm cli temporal workflow list` (namespace `gen9`, as the stack's operator) |
| Backup | `make backup DIR=…` at the root backs up every stack, and `make restore DIR=…` restores it ([docs/operations.md, "Back up and restore"](../docs/operations.md#back-up-and-restore)). A logical dump of Temporal's databases alone: `docker compose exec postgres pg_dumpall -U postgres > temporal.sql` |
| Stop (keeps data) | `docker compose down` |
| Renew the internode certificate | `./init-tls.sh --force`, then `docker compose up -d --wait` |
| Reset (**deletes every workflow**) | `docker compose down -v`, then `./init-env.sh --force` |

## Upgrade

Upgrade one minor version at a time; patch versions can be skipped
([upgrade guide](https://docs.temporal.io/self-hosted-guide/upgrade-server)).
- Bump `temporalio/server` and both `temporalio/admin-tools` images to the same version and re-pin
  their digests (`docker buildx imagetools inspect temporalio/server:<version>`).
- Then `docker compose up -d --wait`: the `schema` job updates the schema before the new server
  starts.
- The web UI's version comes from its own releases.

Read each release's notes first. For example, v1.33 removes the legacy Worker Versioning APIs and
the legacy query converter; Gen9 uses neither.
