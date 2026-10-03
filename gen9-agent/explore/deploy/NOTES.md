# Deploying Gen9 with Docker and Kubernetes: what the probes showed

The probes of docs/plans/deploy.md (R2), run outside the repository in `~/.cache/gen9-probes`
(Docker Desktop on Linux shares only the home folder with its VM, so a probe under `/tmp` fails
with "mounts denied … is not shared from the host"), with throwaway Compose projects, a registry on
127.0.0.1:25000, and clusters `kind-r2c` and `k3d-r2c`. Tools from their release pages, checksums
verified: kubectl v1.37.1, kind v0.33.0, k3d v5.9.0, Helm v4.3.0, kubeconform v0.8.0, helm-diff
v3.15.15; Docker 29.8.1 (Docker Desktop, a VM of 20 CPUs and 17.6 GiB), Compose v5.5.1.

## Compose to Kubernetes with Compose Bridge (R2b)

`docker compose bridge convert` (Compose v5.5.1, transformation `docker/compose-bridge-kubernetes`,
`sha256:8520c842…`) on a copy of `gen9-ui/compose.yaml` with throwaway values:

- As written, it fails: "failed to parse generated yaml /templates/base/deployment.tmpl: yaml: line
  90: did not find expected ',' or ']'". The cause is the healthchecks: their `node -e "fetch(…)"`
  test, with quotes and commas, lands in the template unescaped. Without the healthchecks it
  converts.
- What it makes: a Kustomize base and a `desktop` overlay; every service a Deployment (Valkey too,
  with a PersistentVolumeClaim and `strategy: Recreate`); each Compose network a NetworkPolicy on
  pod labels; published ports as `LoadBalancer` Services named `<service>-published`; images as
  written (`gen9-ui:prod`, `imagePullPolicy: IfNotPresent`).
- Secrets end up in the manifests: `SESSION_SECRET`, the client secret and the Valkey password are
  plain `env` values in the Deployments, and the password is in Valkey's `args`.
- kind's API server refuses it (`kubectl apply --dry-run=server`): the Deployment `prod`:
  "spec.template.spec.restartPolicy: Unsupported value: "unless-stopped": supported values:
  "Always""; `valkey`: "cannot unmarshal bool into Go struct field Container…args of type string"
  (`--appendonly "yes"` came out as a YAML boolean). `kubeconform -strict` caught only the second.
- Network aliases (`gen9-ui`, `gen9-agent`, the names other stacks call) don't become Services.

So Compose can't be the one definition through the default transformation, and our own templates
would be a second chart written in Go templates over Compose's model. The chart is written by hand
(Decision Log, "The chart is written by hand"), with a check that the two shapes agree.

## Local clusters (R2c)

| | kind v0.33.0 | k3d v5.9.0 |
| --- | --- | --- |
| Created in | 56 s | 27 s with `--image rancher/k3s:v1.37.0-k3s1` (its default is k3s v1.35.5+k3s1) |
| Kubernetes, runtime | v1.37.0, containerd 2.3.4 | v1.37.0+k3s1, containerd 2.2.3-k3s1 |
| Memory when idle | 613 MiB | 314 MiB (server) + 18 MiB (load balancer) |
| Storage class | `standard` (default), `rancher.io/local-path`, `WaitForFirstConsumer` | `local-path` (default), the same provisioner |
| Gateway API | no CRDs, no controller | CRDs v1.6.1 (standard channel) from k3s's `gateway-api-crd` chart, and Traefik 3.7.13, but only Traefik's Ingress provider is on: no GatewayClass |
| Ingress | none | Traefik (class `traefik`) |

So neither cluster routes HTTP to a Gateway out of the box, and only k3s routes Ingress. How
traffic gets in is a setting (U5): an HTTPRoute on a Gateway the cluster has, an Ingress of a class
it has, or nothing (a `Service` to port-forward or load-balance).

## Drift on Kubernetes (R2e)

A `helm create` chart installed by Helm v4.3.0 on kind (Helm 4 installs with server-side apply:
the Deployment's managed fields show manager `helm`, operation `Apply`; `helm get metadata`:
`APPLY_METHOD: server-side apply`). Then a change by hand: `kubectl set image` and
`kubectl scale --replicas=3`.

| Command | Unchanged | After the change by hand |
| --- | --- | --- |
| `helm diff upgrade … --detailed-exitcode` | 0 | 0: compares the new render with the release Helm stored, not with the cluster |
| `helm diff upgrade … --three-way-merge --detailed-exitcode` | 0 | 2, naming `r2e, demo, Deployment (apps)`, `replicas: 3` to `1` and the image |
| `helm template … \| kubectl diff --server-side -f -` | | 1, the same lines, plus noise: `generation`, and the chart's test hook (rendered by `template`, never installed) |
| `helm upgrade` (nothing changed in the chart) | | fails: "Apply failed with 2 conflicts: conflicts with "kubectl" with subresource "scale" … conflicts with "kubectl-set"": server-side apply won't take back fields another manager took |
| `helm upgrade --server-side=true --force-conflicts` | | deployed; replicas 1 and the chart's image back; the three-way diff 0 again |

So the drift check is helm-diff's three-way merge, and putting things back is an upgrade with
`--force-conflicts`.

helm-diff under Helm 4: `helm plugin install https://github.com/databus23/helm-diff` fails ("plugin
source does not support verification. Use --verify=false to skip verification"). Installing the
release's tarball with its `.prov` and the maintainer's key works ("Plugin Hash Verified"):
`gh release download v3.15.15 -R databus23/helm-diff -p 'diff-3.15.15-linux-amd64.tgz*'`, the key
from `https://github.com/databus23.gpg` (fingerprint `C5645EF4 7482257A 1F806D2B EA17A2A2
06AFF8CD`, as the README gives it) through `gpg --dearmor` into a keyring file, then
`helm plugin install diff-3.15.15-linux-amd64.tgz --keyring <file>`. `helm plugin list` then says
`mismatched provenance` for it all the same. Plain `curl` of that asset got 504 from GitHub six times
in a row while `gh release download` worked.

## Compose apps as OCI artifacts (R2a)

`docker compose publish` (Compose v5.5.1) to `registry:3` on 127.0.0.1:25000:

- A plain-HTTP registry needs `--insecure-registry` on `publish` (and as a global flag when
  pulling: `docker compose --insecure-registry localhost:25000 -f oci://…`), hidden from `--help`;
  the Docker daemon's own exception for localhost doesn't apply (Compose's resolver,
  `internal/oci/resolver.go`).
- Bind mounts are not refused, despite Docker's page ("refuses … service(s) containing bind
  mounts"): Compose asks "you are about to publish bind mounts declaration … only the bind mount
  declarations will be added to the OCI artifact (not content)" (`pkg/compose/publish.go`), and
  `-y` publishes them. Pulled back, `./site` becomes
  `~/.cache/docker-compose/<hash>/site`, which holds nothing: the files a bind mount carries don't
  travel. Inline `configs:` (`content:`) do.
- `--resolve-image-digests` pins `nginx:1.29-alpine` to
  `docker.io/library/nginx:1.29-alpine@sha256:56168782…` in the artifact.
- Running it: with `${GREETING:-hi}` in the file, `up` asks "Do you want to proceed with these
  variables? [Y/n]" and cancels without a terminal; `up -y` runs it (`-y` is `up`'s, not a global
  flag). The value comes from the environment of the host that runs it.

Drift with Compose, on that project:

| Change | `config --hash '*'` against the container's `com.docker.compose.config-hash` | `up -d -y --dry-run` |
| --- | --- | --- |
| none | equal | `Running` |
| a variable changed on the host (`GREETING=other`) | differs | `Recreated` |
| `docker update --memory 64m` | equal | `Running`: missed |
| a file changed inside the container (`docker exec … > index.html`) | equal | `Running`: missed; `docker diff` lists it |

So a drift command for Docker compares the hash, and also what Compose doesn't: the image digest
running, the settings `docker update` can change (memory, CPUs, restart policy), and containers in
the project that Compose doesn't declare. Containers whose files mustn't change run `read_only`.

## Sandboxes on OpenSandbox's Kubernetes runtime (R2d)

OpenSandbox's all-in-one chart from `release-1.1.0` (`manifests/charts/opensandbox`, chart 1.1.0:
the CRDs in `base`, the controller, the server; `helm dependency build` first) on `kind-r2c`, with
the controller and server images from Docker Hub (`opensandbox/controller:release-1.1.0`,
`opensandbox/server:release-1.1.0`, the same digest `gen9-sandbox/Dockerfile` pins), and Gen9's
execd and egress images loaded with `kind load docker-image` (7 s). The chart's defaults pull from
an Aliyun registry and ask 4 GiB for the server; the probe set the images, 256 MiB, and a
`configToml` with `[runtime] type = "kubernetes"`, `execd_image = "gen9-sandbox-execd:v1.1.0"`,
`[egress] image = "gen9-sandbox-egress:release-1.1.0"`, `mode = "dns+nft"`. Installed and ready
in 32 s.

`sandbox_k8s.py` (the SDK 1.1.0, through the server forwarded to 127.0.0.1:25090):

- The first create failed: "namespaces "opensandbox" not found". The chart doesn't create the
  namespace sandboxes go to (`[kubernetes] namespace`); created by hand, it worked.
- A `BatchSandbox` became one pod with two containers, `sandbox` (`python:3.12-slim`) and
  `egress` (Gen9's image), and an init container `execd-installer` (Gen9's execd image).
- Through the server: `id -u` 0; the allowed host (`pypi.org`) 200; an undeclared host and the
  cloud metadata address blocked (`URLError`). Gen9's egress image, with `deny.always`, works there
  unchanged.

What differs from Gen9's Docker sandboxes, for U6 (the pod's spec):

- `execd-installer` runs `privileged: true`.
- The sandbox container drops only `NET_ADMIN` (Docker's: all capabilities but a few, raw sockets
  among those dropped, `no_new_privileges`), runs as root, and gets 1 CPU and 2 GiB, not Gen9's
  1 CPU and 1 GiB.
- No `runtimeClassName` (gVisor or Kata need `[secure_runtime]` and the runtime on the nodes).
- No NetworkPolicy in the sandboxes' namespace: other pods could reach a sandbox's execd. The
  service account token isn't mounted (`automountServiceAccountToken: false`).
- The pod template comes from `/etc/opensandbox/example.batchsandbox-template.yaml` in the server
  image (`restartPolicy: Never`, tolerating every taint); `[kubernetes] batchsandbox_template_file`
  can point at Gen9's own.

## A Gen9 image from a variable: built here, or pulled by digest (U2)

A throwaway project (`~/.cache/gen9-probes/u2`) with one service that has both `build: app` and
`image: ${WEB_IMAGE:-u2-web:dev}`, and its image pushed to `registry:3` on 127.0.0.1:25000:

| `WEB_IMAGE` | Command | What Compose did |
| --- | --- | --- |
| unset | `up -d --build` | built `u2-web:dev`, as `make up` does today |
| `127.0.0.1:25000/u2/web@sha256:a7fbd7dc…` | `up -d --no-build` | "Pulling", "Pulled"; the container's image is the digest reference |
| the same | `up -d --build` | "failed to solve: build tag cannot contain a digest" |

So a stack keeps its `build:` for development, takes its image from a variable, and `make up`
switches from `--build` to `--no-build` when it has a lock. OpenSandbox's server reads the execd
and egress images only from its TOML (`opensandbox_server/config.py` overrides only the API key,
the Postgres DSN and the secure-access keys from the environment), so `launch.py` writes it a copy
of `config.toml` with the two images set from the environment.

## Stacks as namespaces: the names they call and who may call them (U3)

On `kind-r2c` (kind v0.33.0, kindnet), three namespaces: `u3b` runs a web server behind Service
`web`; `u3a` has Service `gen9-web` of `type: ExternalName`, `externalName:
web.u3b.svc.cluster.local`; `u3c` has nothing.

- From `u3a`, `wget http://gen9-web` answered `hello-from-b`: a short name in the caller's
  namespace reaches another namespace's Service, as a `gen9-<stack>` alias does on a Compose network.
- With a NetworkPolicy in `u3b` allowing ingress only from namespace `u3a`
  (`kubernetes.io/metadata.name`), `u3a` still got through and `u3c` timed out: kindnet enforces
  NetworkPolicy.

### Another stack's names, all of them (U3)

An ExternalName per stack gives only its own name; a stack may give more on its network
(`gen9-models-admin`, `gen9-langfuse-media`, `gen9-mailpit`). On `kind-gen9`, a pod in
`gen9-agent` from gen9-agent's image, with `dnsConfig.searches: [gen9-models.svc.cluster.local,
gen9-langfuse.svc.cluster.local]` and no ExternalName Service:

- `gen9-models-admin` 10.96.38.10 and `gen9-langfuse-media` 10.96.102.47, the cluster IPs of the
  Services of those names in their namespaces; `gen9-models` too; `no-such-name` still "Name or
  service not known".
- Its `/etc/resolv.conf`: `search gen9-agent.svc.cluster.local svc.cluster.local cluster.local
  gen9-models.svc.cluster.local gen9-langfuse.svc.cluster.local`, `options ndots:5`: the
  namespace's own names first, the added ones after, as Kubernetes merges them.

Then the charts with search domains instead (each pod's from the `gen9-*` networks its Compose
service joins): after `make k8s-up`, gen9-agent's migration hook reached `gen9-postgres` with no
ExternalName created first; from the worker, `gen9-models-admin:4001` and
`gen9-langfuse-media:9000` answered (404 and 403 at `/`, the services themselves); from the API,
`gen9-langfuse-media` didn't resolve, as on Docker, where `api` doesn't join gen9-langfuse.

## A chart in the stack's folder, with the files Compose mounts (U3)

Helm v4.3.0, a chart at `stack/chart/` with `chart/initdb -> ../initdb` (a symlink) and a
ConfigMap template ranging over `.Files.Glob "initdb/*"`:

- `helm template p chart` put `01.sql` in the ConfigMap, read through the link.
- `helm package chart` logged "found symbolic link in path. Contents of linked file included and
  used" and the archive held `probe/initdb/01.sql` as a file; `helm template` of the archive gave
  the same ConfigMap.

So each stack's chart can sit in its own folder and link to the configuration Compose bind-mounts
(Keycloak's realm, the initdb scripts, `config.toml`, the router's `config.yaml`…): one copy of
each file in git, carried inside the chart a release publishes.

## A chart that reads its stack's compose.yaml (U3)

Helm v4.3.0, `.Files.Get "compose.yaml" | fromYaml` on a link to `gen9-keycloak/compose.yaml`,
then `gen9-ui/compose.yaml`: parsed whole, YAML anchors and merge keys resolved (gen9-ui's `prod`
got the eleven variables of `<<: *app-env` plus its own, and its `env_file`); image pins, logging
options and literal values such as `KC_LOG_CONSOLE_FORMAT` came through as written.

A helper over a service's `environment` (`~/.cache/gen9-probes/u3-compose/templates/_env.tpl`):
`${X:?…}` (a secret `make setup` writes to `.env`) became `valueFrom: secretKeyRef` on the Secret
`env`; `${X:-default}` became the setting `X` (`--set settings.KC_HOSTNAME=https://auth.example.com`
gave `KC_HOSTNAME` that) or else the default; literals stayed; a secret inside a longer value is
defined first and referred to as `$(X)`, which Kubernetes expands from the container's earlier
variables. For Keycloak: 6 secrets and 17 values, each as Compose would give it.

## Sandboxes with kubernetes-sigs/agent-sandbox (U6)

On `kind-gen9`: agent-sandbox v1.0.4's `sandbox.yaml` (sha256 `c4f6344b…`, equal to the digest
GitHub's API gives for the release asset; controller `registry.k8s.io/agent-sandbox/agent-sandbox-controller:v1.0.4`,
CRD `sandboxes.agents.x-k8s.io`), then OpenSandbox's server alone (its `manifests/charts/server`
at release-1.1.0, image `opensandbox/server:release-1.1.0`) with `[kubernetes] workload_provider =
"agent-sandbox"`, `namespace = "u6-sandboxes"`, `[agent_sandbox] shutdown_policy = "Delete"`, and
Gen9's execd and egress images from the lock.

- The first create was refused: "sandboxes.agents.x-k8s.io is forbidden: User
  "system:serviceaccount:u6-server:u6-server" cannot create resource "sandboxes""; upstream's server
  chart grants nothing on that API group. A Role and RoleBinding in `u6-sandboxes` fixed it.
- Then `sandbox_k8s.py`: `id -u` 0, the allowed host 200, an undeclared host and the metadata
  address blocked (`URLError`). A `Sandbox` (Ready, `DependenciesReady`), its pod (2/2: `sandbox`
  and Gen9's `egress`, init `execd-installer` from Gen9's execd image) and a headless Service.
- The init container ran `privileged: true` here too: `[egress] disable_ipv6` defaults to true
  (`config.py`: "egress IPv6 support is incomplete, especially on Kubernetes runtime"), and
  `prep_execd_init_for_egress` needs privilege to write `/proc/sys/.../disable_ipv6`.

## A reverse proxy in front of the Docker stacks (U5b)

Caddy 2.11.6 (the official image; v2.11.7, released that day, wasn't on Docker Hub yet) as its
own Compose project, joining the shared networks `gen9-ui`, `gen9-keycloak`, `gen9-agent` and
`gen9-langfuse`, publishing 127.0.0.1:80 and :443, `local_certs` (its own CA), one site per host
under `gen9.localhost`, each `reverse_proxy` to the name the stack gives on its network:

- `https://gen9.localhost` (gen9-ui:3000), `id.gen9.localhost/realms/gen9/.well-known/openid-configuration`
  (gen9-keycloak:8080), `api.gen9.localhost/readyz` (gen9-agent:8000) and
  `traces.gen9.localhost/api/public/health` (gen9-langfuse:3000): 200 each, over TLS.
- Without `-k`, curl refused the certificate (`ssl_verify_result` 20): a client trusts Caddy's
  root first (`/data/caddy/pki/authorities/local/root.crt` in its volume), as on any machine with
  no public name; with one, Caddy gets an ACME certificate instead.
- Docker Desktop mounts only the folders it shares: a bind mount from `/tmp` was refused ("mounts
  denied"), from the home folder it worked.
- Streaming: Caddy's `reverse_proxy` docs, `flush_interval`: responses with
  `Content-Type: text/event-stream` are flushed to the client immediately, whatever is set.

## The charts' routes on kind's Gateway (U5b-3)

cloud-provider-kind v0.12.0 as a container on the `kind` network with Docker's socket
(its README's way): it created Gateway API's standard CRDs itself and the GatewayClass
`cloud-provider-kind` (Accepted). Its Gateway is an Envoy container outside the cluster
(`kindccm-gw-…`). With Docker in a VM (Docker Desktop here) the listener's port was published only
with `--enable-lb-port-mapping` (on a random host port): without it, only Envoy's admin port was;
the README's "automatically enabled on platforms where this is required" didn't apply, Docker
Desktop on Linux being taken for plain Linux.

A Gateway `gen9` in `gen9-gateway`, an HTTPS listener on 443 terminating with a self-signed
certificate for `gen9.localhost`, `*.gen9.localhost` and `*.apps.gen9.localhost`, routes from all
namespaces; the five public stacks installed with `global.domain=gen9.localhost` and that Gateway:
seven HTTPRoutes, each Accepted; every host answered 200 through Envoy's published port, the
certificate verified against the test one (`curl --cacert … --connect-to`).

The stacks' NetworkPolicies must let the Gateway's proxy in, and kind's isn't a pod: with
Keycloak's rule for its public port removed and the Gateway made again (so every connection new),
the API answered 200 and Keycloak 503; the rule back (`make k8s-reset`), 200 within 5 s. Removed
with the Gateway's connections already open, nothing changed: the policy is checked only on new
connections. Restarting Envoy's container left it answering 503 until the Gateway was made again:
cloud-provider-kind configures it once.

## Leaving a bundled store out on Docker (U5c)

Compose v5.5.1, a throwaway project (an app that depends on a store, both Valkey's image).

- The store in a profile, the app's `depends_on` with `required: false`, the profile inactive:
  `config --services` lists only the app, and `up --wait` starts it alone. With `required: true`:
  `service "app" depends on undefined service "store": invalid compose project`.
- `COMPOSE_PROFILES=${BUNDLED-store}` in `.env` interpolates: unset, the store runs; `BUNDLED=`
  in the shell, it doesn't. Dify's `docker/.env.example` does the same
  (`COMPOSE_PROFILES=${VECTOR_STORE:-weaviate},${DB_TYPE:-postgresql}`).
- A profile from the setting, `profiles: ["${STORE_HOST:+external}"]`: unset, the profile is `""`
  and the store ran, but only because an unset `COMPOSE_PROFILES` counts as one empty profile;
  with `COMPOSE_PROFILES=local` (or `--profile local`) the store was left out. Not used.
- `scale: ${STORE_SCALE:-1}` set to 0: the app started alone, but the store stays in `config
  --services`, so every script reading the config needs a case for it. Not used.
- A default holding a required variable, `${SESSION_STORE_URL:-redis://:${VALKEY_PASSWORD:?run
  init}@valkey:6379/0}`: with both unset, "required variable VALKEY_PASSWORD is missing a value";
  with `SESSION_STORE_URL` set, its value, and `VALKEY_PASSWORD` isn't needed.
- `docker compose config --environment` prints every variable it interpolates with, values
  included (the shell's and `.env`'s): read it only to test, never print it.
- `docker compose config SERVICE` prints the service and its dependencies, so a label read from it
  may be a dependency's (gen9-ui's `dev` showed `valkey`'s): `make up`'s check reads the label from
  the Compose files themselves.

From a pod on k3d, a container on the cluster's Docker network (`k3d-gen9`) answers at its
container name: CoreDNS forwards to the node's resolver, Docker's.

## Postgres elsewhere for gen9-keycloak, gen9-models and gen9-temporal (U5c-2)

Each pinned image's own settings, read from the image or its source that day: Keycloak 26.7.5
(`kc.sh start --help-all`) has `--db-url-host`, `-port`, `-database`, `-properties` ("appending
the right character at the beginning"), `--db-tls-mode` (disabled, verify-server; the latter
needs the server's certificate or CA in `--db-tls-trust-store-file`); Temporal 1.32.0's embedded
config template (`common/config/config_template_embedded.yaml` at v1.32.0) reads `POSTGRES_SEEDS`,
`DB_PORT`, `SQL_TLS_ENABLED`, `SQL_CA`, `SQL_HOST_VERIFICATION` (default false) for both
databases, and admin-tools 1.32.0's `temporal-sql-tool` its own `SQL_TLS`,
`SQL_TLS_DISABLE_HOST_VERIFICATION`, `SQL_TLS_CA_FILE`; the router's three clients take one URL:
Prisma (LiteLLM) `sslmode` prefer, disable or require (Prisma's PostgreSQL page), psycopg libpq's.
pgjdbc's `sslmode=require`: "In this mode we will accept all server certificates".

A Postgres 16.15 container outside the stacks, TLS only (`ssl=on`, a self-signed certificate,
`pg_hba.conf`: `hostssl … scram-sha-256`, `hostnossl … reject`; a plain connection: "pg_hba.conf
rejects connection … no encryption"), the three roles and four databases made with the SQL in
docs/operations.md. Keycloak with `KC_DB_URL_PROPERTIES=?sslmode=require`, the router with
`LITELLM_DB_SSLMODE=require`, Temporal with `SQL_TLS_ENABLED=true`: every connection in
`pg_stat_ssl` with `ssl` true (keycloak 4, litellm 4, temporal 47 and 6), Keycloak's 100 tables,
LiteLLM's 90, Temporal's 40 and 3, `gen9_admin` made by the keys job (`CREATEROLE`), the admin
API's budget and usage routes 200. Keycloak took `KC_DB_URL_PROPERTIES` empty (the bundled case).

Moving Keycloak to an empty database makes a new realm, with new signing keys: gen9-agent's cached
token for Temporal was then refused ("Request unauthorized", PermissionDenied) until gen9-agent
restarted and took a new one. A recreated Keycloak also spends about 20 s trying to join the
cluster of the container it replaced (JGroups, "too many JOIN attempts (10): becoming singleton"),
which happens on any recreate.

## gen9-agent's database elsewhere (U5c-3)

A server outside the stacks on Gen9's own Postgres image (the lock's gen9-postgres: PostgreSQL 18,
pgvector 0.8.6, pg_textsearch 1.4.0), started with `shared_preload_libraries=pg_textsearch`, TLS
only, its administrator `dbadmin` (not `postgres`), no Gen9 role or database in it until the
docs' SQL made `gen9_agent` and its database. Then, with `GEN9_POSTGRES_SERVER`, `_SERVER_PORT`,
`_SSLMODE=require` and `_ADMIN_USER=dbadmin` in gen9-postgres's `.env`: `make up`'s preflight
refused until `make setup` had copied the first three into gen9-agent's (one line each);
gen9-postgres's jobs created `vector` and `pg_textsearch` and `gen9_agent_app` as `dbadmin`,
gen9-agent's migrations made 22 tables as `gen9_agent`, and every connection of
`gen9_agent_app` was TLS. With `_SSLMODE=disable`, both gen9-postgres's job and gen9-agent's
migrations were refused ("pg_hba.conf rejects connection … no encryption"): the setting reaches
libpq in both stacks. gen9-agent's image has libpq 18.6 (`psycopg.pq.version()`), which knows
`sslrootcert=system` (U5c-5).
