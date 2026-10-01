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
