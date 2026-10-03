# Operating Gen9

How to install Gen9 on your own machine or server, run it day to day, upgrade it, back it up, stop
its agents, and start over. What each stack does is in its own README (the table in the
[README](../README.md#how-it-is-built) links to each); every command below is a `make` target
(`make` lists them).

## Requirements

Docker with Compose 2.24 or newer, GNU Make (macOS's built-in 3.81 is enough), bash, openssl. With gen9-langfuse, give Docker at least 8 GiB of memory: all stacks idle used 5.8 GiB after a day of use (Langfuse 2.9, Keycloak 1.4, the model router 0.6, Temporal 0.5, measured). `make doctor` checks all of this and the ports each stack needs; `make setup` and `make up` run the same checks first and stop before starting anything. It stops a start if a secret is missing from gen9-langfuse/.env (Langfuse's own compose file would fall back to a published default), or if a stack leaves a bundled store out with no setting naming another ([External services](#external-services)). On a running install it also checks Temporal's certificate, and warns of a deletion still running after a day (a step that keeps failing).

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
make diff       # what runs that differs from what's declared: exit 2, naming each
make reset      # put back what differs
```

Every command covers all stacks. `STACKS` narrows it to some, by name from `make stacks` (`gen9-ui` works too); they start in dependency order and stop in reverse, whatever order you list them in:

```bash
make up STACKS="keycloak ui"
make logs STACKS=ui FOLLOW=1      # follow one stack's logs; TAIL=200 for more lines
make config STACKS=agent          # validate its Compose file
```

Each stack labels the services you open (`gen9.name`, `gen9.url` in its Compose file), from the same settings the stack itself uses, so the list stays right when you change a port. `make up` also notes when a stack it starts calls another that isn't running, and restarts a container failing its health check, unhealthy or on its way there (the agent's API after its database was wiped, say), before waiting for it: Compose's `--wait` fails at once on an unhealthy container it doesn't replace (docker/compose#9092), while a restarted one gets its start period again.

`make diff` compares every container with what `make up` would create from the Compose files, the
settings and `images.env`: Compose's hash of its service's configuration (a changed setting,
image, command, mount or port), the image it runs, what `docker update` changes without Compose
knowing (memory, CPUs, processes, restart policy), a service with no container, and a container
no Compose file declares. `make reset` recreates only the services that differ, and removes such
containers. Not compared: files changed inside a running container (`docker diff` lists them,
among what each service writes as it runs). On Kubernetes: `make k8s-diff`, below.

What each service logs, and the records kept on purpose (sign-in events, the audit record, traces), who can read, change or erase them, for how long, and how to send the logs to a separate system: [docs/logging.md](logging.md).

Each stack's own setup (its `init-env.sh` options, such as other ports) is in its README; `make setup` runs them with the defaults. `make up` refuses to start a stack whose files are missing and says which `make setup` writes them.

## Gen9's images: built here, or by digest from a lock

`make up` builds Gen9's own 7 images on this machine from each stack's folder (the other images,
Temporal's, Langfuse's, Postgres's and the rest, are pinned by digest in the Compose files). To
run images built once elsewhere instead, the same ones on every machine, give it a lock: one line
per image, `<registry>/<image>@sha256:<digest>`, as CI writes for each build of `main` (the
`images.lock` artifact of the Images workflow, images in `ghcr.io/coast-guide`) and as a release
will attach.

```bash
make up IMAGES=images.lock        # or a URL; pulls each by digest, builds nothing
make up                           # keeps running the lock's images (images.env)
make up IMAGES=local              # builds them here again
```

`IMAGES` writes `images.env` at the top of the repository (`scripts/images.sh` checks the lock has
each image exactly once, by digest); `make up` reads it into every stack as `GEN9_AGENT_IMAGE`,
`GEN9_UI_IMAGE`, `GEN9_KEYCLOAK_IMAGE`, `GEN9_POSTGRES_IMAGE`, `GEN9_SANDBOX_IMAGE`,
`GEN9_SANDBOX_EGRESS_IMAGE` and `GEN9_SANDBOX_EXECD_IMAGE`, and starts them with `--no-build`.
The machine still needs this repository at the same version as the lock: the stacks' settings and
configuration files come from it. Images of your own, in your registry:

```bash
REGISTRY=registry.example/gen9 TAG=mine docker buildx bake --push   # both platforms (docker-bake.hcl)
for i in gen9-agent gen9-ui gen9-keycloak gen9-postgres gen9-sandbox gen9-sandbox-egress gen9-sandbox-execd; do
  echo "registry.example/gen9/$i@$(docker buildx imagetools inspect registry.example/gen9/$i:mine --format '{{json .Manifest}}' | jq -r .digest)"
done > images.lock
```

## Under a domain, over TLS

Each stack serves on its own `127.0.0.1` port, for this machine. To reach Gen9 from elsewhere, or
under a name with a certificate, `gen9-edge` puts Caddy in front of the stacks, one host per
service people reach (the web app at the domain itself, `id.`, `api.`, `traces.`, `temporal.`…):

```bash
make setup DOMAIN=gen9.example.com     # EDGE_TLS=you@example.com: certificates from Let's Encrypt
make up                                # gen9-edge too, now that it's set up
```

The hosts, the certificates (Let's Encrypt, or Caddy's own CA and how a browser trusts it; the
connector apps' hosts', made on demand), and the settings: [gen9-edge/README.md](../gen9-edge/README.md). Without `DOMAIN`, `make up` leaves
gen9-edge out with a note. On Kubernetes the cluster's Gateway serves the same hosts (below).

## Kubernetes

The same Gen9 runs on any conformant cluster (kind or k3s on a laptop or a VM, or a managed one):
each stack is a Helm release of its own chart, `gen9-<stack>/chart`, in its own namespace
`gen9-<stack>`, from the same images by digest and the same settings files as on Docker.

```bash
make setup                           # the same secrets and settings files as for Docker
make k8s-up IMAGES=images.lock       # kubectl's current context; K8S_CONTEXT=<context> for another
make k8s-diff                        # exit 2, naming each object, if anything differs from what's declared
make k8s-reset                       # put back what was changed by hand: each object replaced as declared
make k8s-down                        # uninstall; volumes and Secrets stay
```

A cluster needs Kubernetes 1.28 or later, a default StorageClass (or `global.storageClass`), and,
on this machine, `kubectl`, Helm 4 and, for `k8s-diff`, the helm-diff plugin. Nothing in the charts
is tied to a cloud: Deployments, StatefulSets, Jobs, Services, ConfigMaps, NetworkPolicies.

Sandboxes (gen9-sandbox) need a little more of the cluster, to hold them as Docker does:

- [kubernetes-sigs/agent-sandbox](https://github.com/kubernetes-sigs/agent-sandbox)'s `Sandbox`
  and its controller, which `make k8s-up` applies from its release, checked against the digest
  pinned in `gen9-sandbox/chart/prerequisites.txt`. OpenSandbox's server, Gen9's own image as on
  Docker, makes each chat's sandbox one, in the namespace `gen9-sandboxes`, and only it may reach
  them there.
- Kubernetes 1.36 or later, for the MutatingAdmissionPolicy that gives each sandbox's container
  the limits it has on Docker (the runtime's seccomp filter, no new privileges, the capabilities
  `gen9-sandbox/config.toml` drops, `SANDBOX_DISK_GB` of disk). On an older cluster the chart
  refuses to install, unless `sandboxes: {hardening: optional}` accepts sandboxes without them.
- Two kubelet settings on the nodes that run sandboxes, which no pod field can set, as
  `deploy/kind.yaml` sets them for kind and `deploy/k3d.yaml` for k3s (a kubelet config file k3s
  takes as `--kubelet-arg=config=…`): a process limit, `podPidsLimit: 4096` (Docker's
  `pids_limit`; the kubelet's default is none), and `singleProcessOOMKill: true`, so that a
  command past a sandbox's memory is killed and the sandbox lives on, as on Docker (on cgroup v2
  the kubelet otherwise kills every process of the container).
- Privileged pods allowed in `gen9-sandboxes` (Pod Security `privileged`, which the chart labels
  it with): each sandbox's first container turns IPv6 off in its pod before it starts, as
  OpenSandbox does on Kubernetes, where its IPv6 egress filtering is incomplete. The sandbox's own
  container is not privileged.

Each chart reads its stack's `compose.yaml`, linked into it: the images, commands, environment,
health checks, configuration files and volumes Docker runs, so the two shapes can't drift apart; the
chart's `values.yaml` adds only what Kubernetes needs (which workload each service is, its ports,
storage sizes). A stack reaches another by the same names as on Docker (`gen9-keycloak`, `gen9-models-admin`):
a service that joins another stack's network `gen9-<x>` there has that stack's namespace among its
pods' DNS search domains here, after its own, so every name given on that network resolves. Each
stack's NetworkPolicy lets in only the stacks that call it.

Settings: `deploy/values.yaml`, or your own file as `K8S_VALUES=<file>`. A setting Compose reads
as `${X:-default}` is `settings.X` there for every stack, or `<stack>.settings.X` for one; a
stack's sizes go under its services (`keycloak: {services: {postgres: {storage: {postgres_data:
50Gi}}}}`). A key the charts don't know fails the install, with its path, and so does a setting no stack's Compose files read (`settings.X` is checked across every stack, `<stack>.settings.X` against that stack's): one misspelt would otherwise install and do nothing. Every setting, with its default, is in gen9-learn's Reference, "Settings". Secrets: each stack's
settings files from `make setup` become Secrets in its namespace (`.env` the Secret `env`,
`keycloak.local.env` the Secret `keycloak-local-env`), applied server-side so no copy of a value
lands in an annotation.

Under a domain: `global.domain` (the same as `make setup DOMAIN=…`, which also writes each
stack's public addresses into the settings files the Secrets come from) and `global.gateway` (the
`name` and `namespace` of a Gateway the cluster has, whose listener holds the certificate) give
each service people reach an HTTPRoute, with the hosts gen9-edge serves on Docker
(`scripts/check-charts.sh` fails if the two differ), and open its port to the Gateway's proxy in
the stack's NetworkPolicy. On kind, `cloud-provider-kind` provides a Gateway (GatewayClass
`cloud-provider-kind`; where Docker runs in a VM, with `--enable-lb-port-mapping`); on k3s,
Traefik's Gateway provider.

To try it here: `kind create cluster --config deploy/kind.yaml` makes a cluster whose containerd
can pull from a registry on this machine (kind's [local registry](https://kind.sigs.k8s.io/docs/user/local-registry/)
recipe: connect the registry to the `kind` network and give each node a `hosts.toml`); `k3d
cluster create --config deploy/k3d.yaml` makes a k3s one (a registry of your own with
`--registry-config`, k3s's `registries.yaml`), whose Traefik serves Gateways on 127.0.0.1's ports
80 and 443 (a listener on its entry points' ports, 8443 for HTTPS). `make
k8s-e2e` runs `make e2e` against the cluster: it forwards each port a stack publishes on Docker to
the same port on 127.0.0.1, and puts `e2e/k8s` first on `PATH`, whose `docker` reaches the pods.

## External services

Each stack runs the stores it needs, as containers with volumes on Docker and as StatefulSets on
Kubernetes. A store can be elsewhere instead (a managed service, a cluster's operator, a server
of your own): one setting points the stack at it, and the bundled one is left out.

- On Docker, the stack's `.env` lists the bundled stores it runs in `COMPOSE_PROFILES`, Compose's
  own setting, which `make setup` writes. Take the store out of that list and put the setting in
  the same `.env`, then `make up`. `make diff` then names the bundled container still running, and
  `make reset` removes it; its volume stays, for `make backup` or `make wipe`.
- On Kubernetes, `<stack>: {services: {<store>: {kind: none}}}` in the values file, and the
  setting in the stack's `.env` (the Secret `env`, since it holds a password) or in
  `<stack>.settings`. The StatefulSet goes; its PersistentVolumeClaim stays, as Kubernetes keeps
  them.
- Either way, a store left out with nothing in its setting is refused: `make up` stops before
  starting anything, and the chart doesn't install, each naming the setting.

| Stack | Store | Setting | What it needs |
| --- | --- | --- | --- |
| gen9-postgres | `postgres`: gen9-agent's database (chats, memory, connectors, schedules) | `GEN9_POSTGRES_SERVER`; `GEN9_POSTGRES_SERVER_PORT` (5432), `GEN9_POSTGRES_SSLMODE` (libpq's: `require` for TLS, `verify-full` to check its certificate; default `prefer`), `GEN9_POSTGRES_SSLROOTCERT` (with `verify-full`: `/etc/gen9/certs/<its CA's file>`, below), `GEN9_POSTGRES_ADMIN_USER` (postgres). `make setup` copies the first four into gen9-agent's `.env`, and `make up` refuses to start while the two differ | PostgreSQL 18 with pgvector and pg_textsearch, which needs `shared_preload_libraries = 'pg_textsearch'` (Gen9's own image has both: running it elsewhere is one way); the database `gen9_agent` owned by the role `gen9_agent`, below, with its password in `GEN9_AGENT_DB_PASSWORD`; an administrator that may create those extensions and roles, `GEN9_POSTGRES_ADMIN_USER` with its password in `POSTGRES_PASSWORD`. On every start gen9-postgres's jobs create the extensions and the role gen9-agent's services use, `gen9_agent_app`, and gen9-agent migrates the database |
| gen9-langfuse | `postgres`, `redis`, `clickhouse` and `minio`: Langfuse's settings and users, its queues, the traces, and the raw events and media | Langfuse's own: `DATABASE_URL` (`?sslmode=require` for TLS); `REDIS_HOST` and `REDIS_PORT` (the password in `REDIS_AUTH`); `CLICKHOUSE_URL` and `CLICKHOUSE_MIGRATION_URL` (the password in `CLICKHOUSE_PASSWORD`, user `CLICKHOUSE_USER`); `LANGFUSE_S3_EVENT_UPLOAD_ENDPOINT`, `LANGFUSE_S3_MEDIA_UPLOAD_ENDPOINT`, `LANGFUSE_S3_BATCH_EXPORT_ENDPOINT`, and Gen9's `LANGFUSE_MEDIA_PUBLIC_URL` and `LANGFUSE_S3_MEDIA_UPLOAD_INTERNAL_ENDPOINT` (the keys in `LANGFUSE_S3_*_SECRET_ACCESS_KEY`, the buckets, regions and the rest by Langfuse's `LANGFUSE_S3_*`). Each store left out alone; `minio` takes `minio-lifecycle` with it | As [Langfuse's own guides](https://langfuse.com/self-hosting/deployment/infrastructure/postgres) ask, at UTC: PostgreSQL 15 or later (16 recommended), a role and database, below; Redis 7 or Valkey 8, with `maxmemory-policy noeviction`; ClickHouse 25.12 or later, with `CLICKHOUSE_CLUSTER_ENABLED=false` for one node, and the user's grants Langfuse lists; an S3 bucket (`langfuse` by default), with Gen9's expiry for the raw events, below. `make setup DOMAIN=…` then leaves the media addresses alone, and gen9-agent uploads a trace's images to the bucket's own address |
| gen9-keycloak | `postgres`: realms, users, sessions | `KC_DB_URL_HOST`; Keycloak's own `KC_DB_URL_PORT` (5432), `KC_DB_URL_DATABASE` and `KC_DB_USERNAME` (keycloak), `KC_DB_URL_PROPERTIES` (`?sslmode=require` for TLS; `?sslmode=verify-full&sslrootcert=/etc/gen9/certs/<its CA's file>` to check its certificate) | PostgreSQL 14 to 18: a role and a database of its own, below; the password in `KC_DB_PASSWORD` |
| gen9-models | `postgres`: the router's keys, budgets and spend | `LITELLM_DB_HOST`; `LITELLM_DB_PORT` (5432), `LITELLM_DB_SSLMODE` (`require` for TLS; default `prefer`), `LITELLM_DB_SSLROOTCERT` (`/etc/gen9/certs/<its CA's file>`: its certificate and name checked, by each client in its own terms) | PostgreSQL: the role `litellm`, allowed to create roles (it makes the admin API's `gen9_admin` and keeps its rights), with a database `litellm` it owns; the password in `POSTGRES_PASSWORD`, percent-encoded if it holds a character URLs reserve |
| gen9-temporal | `postgres`: workflows, their histories and the index that lists them | Temporal's own `POSTGRES_SEEDS` (the host), `DB_PORT` (5432), `SQL_TLS_ENABLED` (`true` for TLS), `SQL_HOST_VERIFICATION` (`true`) and `SQL_CA` (`/etc/gen9/certs/<its CA's file>`) to check its certificate | PostgreSQL 12 or later: the role `temporal` with the databases `temporal` and `temporal_visibility`, and `btree_gin` in the second; the password in `TEMPORAL_DB_PASSWORD`. The schema job fills both on every start |
| gen9-ui | `valkey`: sessions, sign-ins in progress, the logout notices already used | `SESSION_STORE_URL`, `redis://:<password>@<host>:6379/0` (`rediss://` over TLS) | Valkey or Redis. Every key has a time to live, so the bundled one evicts the keys closest to expiring when full (`maxmemory-policy volatile-ttl`, 256 MB, about 60,000 sessions); give another the same |

On a Postgres of your own, as its administrator, with the passwords from the stacks' `.env` (psql
variables, so they don't land in your shell's history):

```sql
-- psql -v a=… -v kc=… -v lf=… -v ll=… -v t=… (gen9-postgres' GEN9_AGENT_DB_PASSWORD,
-- KC_DB_PASSWORD, a new one for Langfuse, gen9-models' POSTGRES_PASSWORD, TEMPORAL_DB_PASSWORD);
-- each stack's lines alone, for one
CREATE ROLE gen9_agent LOGIN PASSWORD :'a';
CREATE DATABASE gen9_agent OWNER gen9_agent;
REVOKE ALL ON DATABASE gen9_agent FROM PUBLIC;
CREATE ROLE keycloak LOGIN PASSWORD :'kc';
CREATE DATABASE keycloak OWNER keycloak;
CREATE ROLE litellm LOGIN CREATEROLE PASSWORD :'ll';
CREATE DATABASE litellm OWNER litellm;
CREATE ROLE langfuse LOGIN PASSWORD :'lf';   -- DATABASE_URL: postgresql://langfuse:…@<host>:5432/langfuse
CREATE DATABASE langfuse OWNER langfuse;
CREATE ROLE temporal LOGIN PASSWORD :'t';
CREATE DATABASE temporal OWNER temporal;
CREATE DATABASE temporal_visibility OWNER temporal;
\connect temporal_visibility
CREATE EXTENSION IF NOT EXISTS btree_gin;
```

Langfuse's raw events (`events/` in its bucket) hold what people wrote; the bundled MinIO deletes
them a day after they're written (`minio-lifecycle`). Give a bucket elsewhere the same rule, as its
own tools take it: `{"Rules":[{"ID":"gen9-expire-raw-events","Status":"Enabled","Filter":{"Prefix":"events/"},"Expiration":{"Days":1}}]}`
(S3's lifecycle configuration; `mc ilm import` for MinIO).

**Move gen9-keycloak's database with its data.** gen9-agent removes the data of people Keycloak no
longer has (its `sweep-deleted-users` schedule, every 15 minutes), and to an empty realm everyone
is gone. Before its guard it ran the account deletion for each, erasing their chats (found while
testing this, docs/plans/deploy.md, Surprises); now a sweep that would delete more than half of the
people Gen9 knows deletes nobody and says so, in gen9-agent's log and as `account.sweep.held`
(gen9-agent/README.md, "Mass deletions held"). Still, copy Keycloak's database before the switch,
or stop gen9-agent first (`make down STACKS=agent`): an empty realm signs everyone out.

The other store starts empty: a stack moved to it starts over (gen9-agent with no chats,
Keycloak with its seeded users only, the router with its keys made again, Temporal with no
workflows, Langfuse with its first user and project only), unless you copy the
bundled one's data into it first (for Postgres, `pg_dump` from the bundled container and
`pg_restore` into yours). With TLS (`sslmode=require`, `SQL_TLS_ENABLED`), the connection is
encrypted. To have the server's certificate checked as well, against the CA that signed it, put
that CA's file (PEM) in `certs/` at the repository's root: the services that reach a store mount
the folder read-only at `/etc/gen9/certs`, and `make k8s-up` makes it the ConfigMap `certs` in
each namespace, which the charts mount there too (a cluster may make its own of that name, for a
chart installed from its release). gen9-postgres's checks it so far (`GEN9_POSTGRES_SSLMODE=verify-full`,
`GEN9_POSTGRES_SSLROOTCERT`): its name too, so `GEN9_POSTGRES_SERVER` must be one the certificate
holds; so do Keycloak's, the router's and Temporal's, by the settings in the table. Langfuse's and
gen9-ui's come next (docs/plans/deploy.md, U5c-5).

A store elsewhere is backed up by whoever runs it: `make backup` copies the bundled stores'
volumes.

## Upgrade

1. `make backup DIR=~/gen9-backup-before-upgrade`, to go back if you need to: an older Gen9 refuses a database a newer one migrated (its `/readyz` says so), and an older ClickHouse may not open the traces a newer one wrote (gen9-langfuse/README.md).
2. `git pull`, or check out the version you want.
3. `make setup`: it adds what the new version needs (new settings, a database role, a budget) and keeps every secret you have.
4. `make up`: it builds the new images (or, with `IMAGES=<the new version's lock>`, pulls them), applies the database's migrations before the agent starts, then replaces the containers. A turn that is running when its worker is replaced goes on in the new one, from its last checkpoint; the web app and the terminal say it restarted.

## Images: SBOMs and known vulnerabilities

`make audit` reads the apps' locked dependencies. What the images hold besides, their OS packages,
the Python or Node their bases install, and what the services Gen9 runs as published bring, is
read by these two (`scripts/sbom.sh`; docs/plans/manual-e2e.md, P6-D1):

```bash
make sbom     # a CycloneDX SBOM of every image the stacks build or run, in scripts/sbom/out/
make scan     # known vulnerabilities in them: those with a fix, failing on a high or critical one
```

- **What they read:** every image of every stack, the profiles' too when they're on this
  machine, and the two OpenSandbox starts for each chat (its execd and the environment's image).
  `STACKS` narrows `make sbom` to some.
- **The tools:** Syft makes the SBOMs and Grype scans them. Both come from their release
  tarballs, fetched by the checksums Anchore's signed checksum files give
  (`scripts/sbom/Dockerfile`, which says how to update them), and run in a container of their own,
  `gen9-sbom`. That container never gets Docker's socket: each image reaches it as `docker
  save`'s archive. Nor does it have a network while it reads one; only Grype's database update
  does.
- **What fails:** a High or Critical vulnerability with a fix that `scripts/sbom/grype.yaml`
  doesn't accept. An acceptance names the version and the reason (gosu's Go, which govulncheck
  shows its code never calls, is one), so the next version ends it. Those without a fix aren't
  listed: only the image's publisher can fix them; `make updates` shows when one has.
- **Grype's database** is fetched again before each scan into the volume `gen9-sbom-grype-db`:
  about 3 GB. It is built once a day, so an advisory of the last day may not be in it yet.

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

On Kubernetes: `make k8s-stop-agents` and `make k8s-resume-agents`, the same in the cluster
(the worker's Deployment scaled to 0, then back to 1; meanwhile `make k8s-diff` names its
replicas, and `make k8s-up` starts it again, as `make up` does on Docker). Both are in the audit
log (`operator.stop`, `operator.resume`). For one person, disable their
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
| Every container's log | Requests, the audit record's lines, and Keycloak's sign-ins and sign-in failures (the user and their address) | Three files of 10 MB a container, compressed, the oldest lines going first (Docker's `local` driver, `x-logging` in each Compose file, and gen9-sandbox's `launch.py` for the chats' environments; Docker's default keeps a log unbounded) |
| `make scan`'s vulnerability database (the volume `gen9-sbom-grype-db`) | Nothing: replaced at each scan | About 3 GB; `docker volume rm gen9-sbom-grype-db` frees it until the next scan |
| A chat's environment (its container's writable layer) | What its commands write | `SANDBOX_DISK_GB` (10) each: one past it is deleted (gen9-sandbox's `launch.py`), and each ends 30 minutes after its last use |

`docker system df -v` lists each volume's size.
