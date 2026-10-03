# Deploy: the same Gen9 anywhere, with Docker or Kubernetes, and proper releases

## Standing instructions (critical: read first, every session)

Given by the owner, for this plan and for every session that resumes it:

1. **The objective, in the owner's words** (via /rigor): "setup a drift free deployment commands
   both for k8s and docker … i don't care about any cloud, if i have k8s setup you should be able
   to run that exact thing in my local and on any cloud. same with docker. also containerized
   docker images is what k8s pod isn't it … so i can run the dockers in any machine, in local or
   vm, or i can run the k8s container in any machine or vm or k8s infra i don't care shouldn't be
   any difference (some things maybe which is configurable)". Then: "i need proper release
   management versioning on github and other stuff i might be missing i don't know for a proper
   release - a research task for that list". Then: "everything should be leveraged as of oct 1
   2026, stale knowledge is crime", and "start the loop".
2. **Today's sources only.** Before each unit: establish the date, query each tool's version from
   its release page, read the vendor's docs for that version, filter out AI-written summaries
   (AGENTS.md, "Before any new piece of work"). A version or behaviour written here is "as of" the
   date in its Decision Log entry; re-check it before relying on it later.
3. **A loop of units, autonomous.** Research, reason, plan here, build, verify live (Docker on
   this machine, and a local Kubernetes cluster: kind and k3d), then one pull request per unit.
   The owner isn't there to ask; decide from the evidence and write the decision down. A session
   keep-alive cron (`2-59/5 * * * *`, re-created by any session that finds none: session jobs die
   with the session and expire after 7 days) re-reads this file and continues the first unchecked
   item.
4. **Outward-facing steps wait for the owner**: publishing a release (pushing the first `v*`
   tag), making a GHCR package public, changing repository settings. Prepare them, verify them on
   a fork-safe path (a pre-release tag on a branch, or a dry run), and list them in Progress as
   the owner's.
5. **Model spend.** This work needs few model calls: `make e2e` against each deployment is the
   costly part. Run it once per deployment shape, measure the router's spend before and after
   (manual-e2e.md, Validation).
6. **Nothing here touches the running stacks** until a unit needs them: probes run in a scratch
   directory with their own Compose project names, ports, volumes and clusters.

## Purpose

Someone with Docker on any machine, or with any conformant Kubernetes cluster (kind or k3s on a
laptop or a VM, or a managed cluster on any cloud), runs Gen9 from a release with one command, and
gets exactly what was tested: the same images, by digest, configured only at a small, documented
set of settings. A second command says whether what runs still matches what the release (or the
git checkout) declares. Each release is a version on GitHub with notes, signed provenance and
SBOMs, that nobody can change afterwards.

How to see it working: deploy a release with Docker on this machine and on a fresh VM, and with
Helm on kind and on k3s; `make e2e` passes against each; the image digests running are the same
in all four; the drift command passes, then names the change after someone edits a container or
a Deployment by hand; `gh release verify` and `gh attestation verify` pass on the release.

What the owner asked, restated before starting (they went on to "start the loop"):

1. Same artifacts everywhere: every Gen9 image built once, published to a registry, referenced by
   digest. A Kubernetes pod runs the same OCI image Docker does (containerd and Docker share the
   image format).
2. Two deployment shapes from one definition: Docker Compose (any machine or VM) and Kubernetes
   (any conformant cluster), on standard APIs only, nothing cloud-specific.
3. Drift-free: git declares what runs; rendering is deterministic; a command shows any difference.
4. A small explicit configuration surface: domain and TLS, storage, replicas and resources, where
   secrets come from, bundled or external databases, the sandbox runtime.
5. Proper release management, and the list of everything a proper release needs (R1).

## Progress

- [x] R0 This plan, its acceptance list, and AGENTS.md pointing at it.
- [x] R1 Release management, the research list the owner asked for: each item decided from
  today's sources in the Decision Log ("Release management (R1)"), then built in U7.
  - Versioning scheme and what one version covers (images, chart, Compose bundle, CLI).
  - Tags and their protection; immutable releases; release attestations; `gh release verify`.
  - Release notes and changelog with prose commit subjects (AGENTS.md's rule), PR labels.
  - The release workflow: draft, assets, publish; a `release` environment with the owner as
    required reviewer.
  - Signing and provenance of every artifact (images, chart, Compose bundle); SBOMs as assets.
  - Upgrade notes and migrations per release (Postgres schemas, Keycloak realm, Temporal).
  - Support and security policy (`SECURITY.md`: which versions get fixes), deprecations.
  - The running version visible (API, web app, CLI), so drift is checkable by version too.
  - OpenSSF Scorecard and what it finds; the Best Practices badge.
  - Anything else today's sources list that Gen9 lacks.
- [x] R2 Probes, outside the repository (scratch directory, throwaway projects and clusters),
  written into `gen9-agent/explore/deploy/NOTES.md`:
  - [x] R2a `docker compose publish` on one Gen9 stack: what it refuses (bind mounts: Gen9
    mounts 16 files and folders, and the Docker socket; `configs:`), `--resolve-image-digests`,
    running it back with `docker compose -f oci://…`, what a multi-project deployment (8 Compose
    projects joined by networks) needs.
  - [x] R2b `docker compose bridge convert` on one Gen9 stack: what it makes (Deployments or
    StatefulSets, Secrets, networks to NetworkPolicies, volumes), with Docker's default
    transformation and with templates of our own; whether Compose can stay the one definition.
  - [x] R2c kind and k3d on this machine: create, load or pull images, a Gateway API controller,
    delete; time, memory and disk.
  - [x] R2d OpenSandbox's Kubernetes runtime on kind: its CRDs and controller chart, the server
    with `[kubernetes]`, a sandbox pod with Gen9's egress image, gVisor or not.
  - [x] R2e Drift on Kubernetes: `helm diff upgrade` (and its three-way mode) and
    `kubectl diff --server-side`, each against an edit made by hand.
- [x] R3 Each third-party part's own official deployment guides, of the version Gen9 pins, read
  that day (the owner: "for third party stacks we are using refer to their own official docs to
  see if they provide guides - latest research"), findings and links in the Decision Log
  ("Third-party guides"), before U3 chooses adopt, adapt or build for each:
  - [x] Keycloak 26.7.5 (container guide, Operator, production configuration).
  - [x] Temporal server 1.32.0 and UI 2.54.1 (self-hosted guide, the Helm chart, production
    checklist).
  - [x] Langfuse 4.48.0 (self-hosting: Docker Compose, Kubernetes Helm; ClickHouse, S3, Redis,
    Postgres requirements).
  - [x] LiteLLM proxy v1.103.1 (Docker, Helm chart, production settings).
  - [x] OpenSandbox 1.1.0 (Kubernetes deployment, controller, secure runtime).
  - [x] PostgreSQL 18 and 16/17 with pgvector (the image's docs; CloudNativePG as the operator).
  - [x] Valkey 9.1, Redis 7.4 (the official images, valkey-helm).
  - [x] ClickHouse 26.8 (its Docker image, its Kubernetes operator).
  - [x] MinIO (Chainguard image) and the S3 alternatives Langfuse documents.
  - [x] SearXNG, Mailpit, Ollama and llama.cpp server (their container docs).
- [ ] U1 Images built once: a bake file for the 7 Gen9 images from the Dockerfiles Compose builds;
  a workflow that builds them for linux/amd64 and linux/arm64, pushes them to
  `ghcr.io/coast-guide/gen9-*`, and attests provenance and SBOM; the digests written down as a
  lock. `make up` keeps building locally for development.
  - [x] `docker-bake.hcl`; `scripts/check-images.py` in `make config` (bake and Compose agree).
  - [x] `.github/workflows/images.yml`: Docker's bake workflow per image; on `main`, push and
    attest; the lock as the run's artifact.
  - [x] Verified here: `docker buildx bake --print`; all 7 built for linux/amd64 (24 s, 57 steps
    from the Compose builds' cache), and gen9-postgres, the egress and execd images for
    linux/arm64 under emulation (318 s; gen9-postgres took `pg-textsearch-…-arm64.zip` by
    `TARGETARCH`); `make config` passes, and fails on a context changed on purpose.
  - [x] Verified in CI: the pull request built all 7 for both platforms on native runners, 14
    jobs, 36 to 115 s each, the run 3 min (run 36871230604 failed first: Surprises, "a Dockerfile
    frontend that knows `source.git.checksum`"; then run 36871669043).
  - [ ] After the owner merges: the run on `main` pushes, signs and attests;
    `gh attestation verify` on each.
- [ ] U1b Langfuse's S3 store: MinIO's repository is archived ("THIS REPOSITORY IS NO LONGER
  MAINTAINED"); choose a maintained store for both shapes (SeaweedFS, which Langfuse's chart
  bundles, or another), or keep Chainguard's build knowingly; any S3 stays a setting.
- [ ] U2 Docker from published images: one command deploys a version (or the lock) on any host
  with Docker, pulling by digest, building nothing; the settings in one place.
  - [x] Probe: a service with `image: ${VAR:-local tag}` and `build:`, given a digest reference,
    pulled by `up --no-build`, never built; `up --build` refuses it ("build tag cannot contain a
    digest") (explore/deploy/NOTES.md, U2).
  - [x] Each service on a Gen9 image takes it from `GEN9_<IMAGE>_IMAGE` (default: today's local
    tag, so `make up` builds as before); `launch.py` gives OpenSandbox the execd and egress images
    from the same variables, since its config takes no environment override for them.
  - [x] `make up IMAGES=<lock file or URL>`: the variables from the lock into `images.env`, read
    by every stack; pull by digest, build nothing. A lock of one's own images, in one's own
    registry: docs/operations.md (the bake file's `REGISTRY` and `TAG`, then one line per image),
    rather than a make target.
  - [x] Verified here: the 7 images built from this branch and pushed to a throwaway registry
    (`registry:3` on 127.0.0.1:25000), a lock of their digests; every stack down, Gen9's local
    tags removed; `make up IMAGES=<lock>`: every Gen9 service pulled by digest, 0 builds, every
    stack healthy; the sandbox server loaded the copy with the lock's execd and egress images
    (after the fix in Surprises). `make e2e` against it, in three runs because the first stopped
    at the failure the fix closed: 18 scripts, 208 checks; then `environments` to `background`,
    112; then `background` to `a11y`, 404, none failed. The router's spend over all three:
    $0.209642, 477 calls.
  - [ ] From GHCR once `main` has pushed (after the owner merges U1).
- [ ] U3 Kubernetes: a Helm 4 chart per stack, standard APIs only, the same images by digest,
  published to GHCR as OCI charts; verified on kind and on k3d (k3s).
  - [x] `deploy/helm/gen9-lib`, a library chart: `gen9.stack` renders a stack from its
    `compose.yaml` (images, commands, environment, health checks, files, volumes, aliases) and the
    chart's `services` values (kind, ports, storage, resources); the names of the stacks it calls
    (its pods' DNS search domains) and the NetworkPolicy of who may call it, from its networks.
  - [x] A chart per stack but gen9-sandbox (U6), each `compose.yaml` linked in: postgres (8
    resources), keycloak (13), langfuse (17, from Langfuse's file and Gen9's override merged as
    Compose merges them), temporal (13, its schema an init container), models (16), agent (12,
    its migrations a pre-install hook), ui (10). `helm lint`; `kubeconform -strict` against
    Kubernetes 1.37.1: all valid.
  - [x] `scripts/k8s.sh up|diff|down`: namespaces, a Secret per settings file (server-side
    apply), `helm upgrade --install --server-side=true --force-conflicts --wait` with the lock's
    images; `diff` by helm-diff's three-way merge and the Secrets by hash.
  - [x] `deploy/helm/values.schema.json`, linked into every chart: `helm template` refused a
    misspelt key (`additional properties 'storge' not allowed`), an unknown stack
    (`'keycloack'`) and a size Kubernetes can't read (`'50GB' does not match pattern`).
    `deploy/values.yaml`: shared `settings.X`, a stack's own `<stack>.settings` and
    `<stack>.services`, checked to reach only that stack.
  - [x] `scripts/check-charts.sh` (every chart: `helm lint`, `kubeconform -strict`), and a CI job
    for it with Helm and kubeconform from their releases, checked against their checksums.
    What the eight charts render (2026-10-03): Deployment 12, StatefulSet 11, Job 7, Service 33,
    ConfigMap 13, NetworkPolicy 9, Namespace, ServiceAccount, Role, RoleBinding, ClusterRole,
    ClusterRoleBinding, MutatingAdmissionPolicy and its binding: Kubernetes' own APIs only;
    agent-sandbox's `Sandbox`es are made at run time by OpenSandbox's server, from the pinned
    prerequisite.
  - [x] `e2e/k8s/docker`, a `docker` for the checks against a cluster (Decision Log); one-off
    containers (`compose run`) as pods rendered from the stack's chart (Surprises).
  - [x] `make k8s-up|k8s-diff|k8s-reset|k8s-down|k8s-e2e`, docs (operations.md "Kubernetes",
    development.md, AGENTS.md's checks, gen9-learn's Reference).
  - [x] Installed on kind (v0.33.0, Kubernetes 1.37.0) from the same lock as Docker
    (`deploy/kind.yaml`, the registry joined to the `kind` network, a `hosts.toml` for
    `127.0.0.1:25000`; the node pulled the lock's digests): the seven stacks, 22 pods ready, in
    725 s from nothing (third-party images pulled from their registries); the Docker stacks down
    meanwhile, data kept. The ports Docker publishes forwarded to the same localhost ports: the web
    app, the agent's `/readyz`, Keycloak's realm, Temporal's UI and Langfuse all 200. e2e's
    `stacks.mjs` through `e2e/k8s/docker`, all passed: password sign-in and the admin's second
    step through Keycloak, the agent's answer through the router, the chat in Postgres, its trace
    in Langfuse, Keycloak's back-channel logout reaching gen9-ui.
  - [x] Verified on kind: the Docker stacks down, a cluster with the lock's registry
    (`containerdConfigPatches` and `hosts.toml`, kind's documented local registry), every chart
    from the lock, port-forwards to the same localhost ports, `make e2e` (with U6). On
    2026-10-03, `make k8s-e2e` in parts, each part resumed where a failure had stopped it once
    its cause was fixed (Surprises: the names a stack calls, one-off containers, the router's
    timeout, the operator's stop, the sweep's audit lines): every one of the 49 scripts passed,
    `stacks`, `temporal` and `runs` again after the names became search domains (`runs` once
    more, Surprises). gen9-agent ran the lock's image with #65 and #67 merged in; every Gen9
    image on the cluster, the sandboxes' egress and execd included, was the lock's digest. The
    cluster's router: $0.224666 over 497 calls for all of it, a floor (deleting the throwaway
    people erases their records).
  - [x] Verified on k3d with the same values: k3d v5.9.0, k3s v1.37.1 (`deploy/k3d.yaml`, its
    kubelet settings from a file k3s takes as `--kubelet-arg=config=…`: `podPidsLimit 4096`,
    `singleProcessOOMKill true` on the node), the kind lock's registry by k3s's `registries.yaml`.
    `make k8s-up` installed the eight stacks in 502 s once the one-shot Jobs restarted in place
    (Surprises); `make k8s-diff` 0; every Gen9 image, the sandboxes' included, the lock's digest.
    `make k8s-e2e`'s scripts that exercise what differs between clusters, once each, to spare
    model spend after all 49 had passed on kind: `stacks`, `temporal`, `models` (after the
    Langfuse fix, Surprises), `search` (a one-off container), `environments` (sandboxes under
    kube-router's policies), `retry` (the router down), `stop`, `context` (a second worker): all
    passed. The router: $0.025507 over 71 calls.
  - [x] The same digests in both shapes (acceptance `same-digests`), one lock
    (`gen9-int.lock`: U2's, with gen9-agent rebuilt with #65 and #67): on k3d, every Gen9
    container's `imageID` the lock's digest, the sandboxes' egress and execd included; on Docker
    after `make up`, every Gen9 container's reference and its image's `RepoDigests` the lock's
    for the five that always run (egress and execd run only in a sandbox: U2 saw the server take
    the lock's, the same two digests).
  - [x] A check that every Compose service has a chart entry and every bind source a link: the
    template's own, run by CI's `charts` job on every pull request and by `make k8s-up`, rather
    than in `make config`, which would need Helm of everyone on Docker alone. Tried on a copy of
    gen9-ui's chart: a service added to its `compose.yaml` failed the render ("compose.yaml's
    extra has no entry in services: give it a kind (none to leave it out)"), and given a kind,
    its unlinked file did ("extra mounts ./not-linked.conf: link it into the chart").
- [x] U4 Drift: one command per shape (`make diff`, `make k8s-diff`) that passes on a fresh
  deployment and names the service after a change by hand; and one that puts it back (`make
  reset`, `make k8s-reset`).
  - [x] Kubernetes, `make k8s-diff`: helm-diff's three-way merge without hooks (the one-shot Jobs
    are gone once done, as they should be), the names a stack calls (hooks, until they became
    search domains in the pods' spec, which helm-diff compares) by `kubectl diff
    --server-side`, each Secret against its settings file by hash, and any field of the stack's
    objects owned by a manager other than Helm and the cluster's controllers (`managedFields`).
    `make k8s-reset` puts everything back: Secrets replaced whole, objects by
    `helm upgrade --server-side=false --force-replace` (a PUT of each, as rendered).
    Verified on kind: 0 on the fresh install (5.6 s for seven stacks); after `kubectl scale`,
    `kubectl set env`, deleting a name Service and a key added to a Secret, exit 2 naming each
    (`gen9-ui, prod, Deployment (apps) has changed: replicas`, `Deployment/api: changed by
    kubectl-set (Update)`, the missing `gen9-keycloak`, `secret env differs from gen9-ui/.env`);
    `make k8s-up` put back two (a server-side apply keeps what other managers added), `make
    k8s-reset` the rest, the Service's cluster IP kept through the replace; then 0, and 0 again
    after a normal `k8s-up`.
  - [x] Kubernetes again, the names now search domains: `kubectl patch` removing gen9-agent's
    API's `dnsConfig`, `make k8s-diff` exit 2 naming its four search domains; `make k8s-reset`,
    then 0.
  - [x] Docker, `make diff` and `make reset` (`scripts/drift.py`): each container against Compose's
    hash of its service, its image, what `docker update` changes (memory, CPUs, processes, restart
    policy), services with no container and containers not declared; `reset` recreates only the
    services that differ (`up --force-recreate --no-deps`, built or by digest as `make up`) and
    removes the undeclared. Verify: 0 on a fresh `make up`; after `docker update`, a setting
    changed in a `.env`, a stopped service and a stray container, exit 2 naming each; `make
    reset`, then 0.
    Verified 2026-10-03, every stack up from the lock by `make up`: 0 ("as declared", eight
    stacks in 0.25 s). Then `docker update --memory 512m` on gen9-ui's `prod`, gen9-temporal's
    `ui` stopped, a container labelled as gen9-ui's, and `VALKEY_MAXMEMORY=300mb` in the
    environment: exit 2, "prod: Memory is 536870912, declared 0", "ui: exited, not running",
    "gen9-ui-stray: not a service of gen9-ui's Compose file", "valkey: its configuration changed
    since it was created". `make reset` (6.5 s): those three recreated, the stray removed; Valkey's
    `maxmemory` 314572800, then 0. Without the setting: exit 2 naming valkey; `make reset`, back to
    268435456; 0.
- [ ] U5 The configuration surface: every setting listed once (domain and TLS, storage class and
  sizes, replicas and resources, secrets source, bundled or external Postgres, Valkey,
  ClickHouse and S3, sandbox runtime), with a schema that rejects unknown keys.
  - Research, started 2026-10-03: the settings are Compose's variables (`${X:-default}`, the same
    names as `settings.X` on Kubernetes) and the settings files' keys. Compose v5.5.1 lists a
    file's variables itself, `docker compose config --variables --format json` (name, default,
    the `:+` value, required): 187 across the stacks today, 108 of them gen9-langfuse's (Langfuse's
    own file). The values tools Helm charts use document `values.yaml` keys (helm-docs v1.14.2,
    last release 2024-07; Bitnami's readme-generator-for-helm 3.0.1; helm-values-schema-json
    v2.6.0; helm-schema 0.23.5), which here are mostly one free map, `settings`. gen9-learn's
    Reference already checks gen9-agent's settings and every settings file's keys
    (`reference.mjs`). To decide: one reference from Compose's own list, checked like the
    Reference, rather than a second copy.
- [ ] U6 Sandboxes on Kubernetes: OpenSandbox's Kubernetes runtime, Gen9's egress and execd
  images, its limits and closed network as on Docker; a chat's command runs in a pod.
  - [x] Choose the workload provider from evidence: OpenSandbox's own `BatchSandbox` (its CRDs
    and controller) or `kubernetes-sigs/agent-sandbox` (SIG Apps, v1.0.4, `Sandbox` CRD), which
    OpenSandbox's server also drives (`workload_provider = "agent-sandbox"`); each tried on kind
    with Gen9's egress sidecar, network policy and limits. Read so far (release-1.1.0's
    `server/opensandbox_server/services/k8s`): both providers add the egress sidecar the same
    way (`egress_helper.apply_egress_to_spec`, the credential proxy too), and both follow an
    expiry (agent-sandbox's `shutdownTime`). R2d's privileged init container comes only from
    `[egress] disable_ipv6`, which writes `/proc/sys/.../disable_ipv6` before installing execd
    (`prep_execd_init_for_egress`). Tried on kind (explore/deploy/NOTES.md, U6): agent-sandbox
    v1.0.4 (its `sandbox.yaml`, checked against the digest GitHub records for the asset; the
    controller from `registry.k8s.io`) with OpenSandbox's server (`workload_provider =
    "agent-sandbox"`) and Gen9's execd and egress images from the lock: `id -u` 0, the allowed
    host 200, an undeclared host and the metadata address blocked, as BatchSandbox in R2d. The
    init container was privileged all the same: `disable_ipv6` defaults to true ("egress IPv6
    support is incomplete, especially on Kubernetes runtime", `config.py`). Upstream's server
    chart grants nothing on `agents.x-k8s.io`: the create was refused (403) until a Role in the
    sandboxes' namespace allowed it. Decision: agent-sandbox (Decision Log, U6).
  - [x] agent-sandbox's CRD and controller from its release file, pinned by its digest
    (`gen9-sandbox/chart/prerequisites.txt`) and checked before `make k8s-up` applies it
    (server-side) and `make k8s-diff` compares it.
  - [x] gen9-sandbox's chart: the server from Gen9's own image with `launch.py`'s Docker parts
    off on the Kubernetes runtime; its config with `[runtime] type = "kubernetes"`, Gen9's execd
    and egress images, Gen9's pod template (no privileged init container, capabilities dropped,
    1 CPU and 1 GiB as `SANDBOX_CPU` and `SANDBOX_MEMORY`); rights only in the sandboxes'
    namespace (upstream's chart gives the server a ClusterRole that creates pods and Secrets
    anywhere); a NetworkPolicy letting only the server reach a sandbox's pod (gen9-agent reaches
    sandboxes only through it, `use_server_proxy`); gVisor or Kata as a setting
    (`[secure_runtime] k8s_runtime_class`). Built: `launch.py` (GEN9_SANDBOX_RUNTIME=kubernetes:
    the config's `[runtime] type`, `[kubernetes]` and `[agent_sandbox]`; no Docker patches or disk
    watcher); the stack template's `dropMounts`, `env` and `serviceAccount`; and
    `templates/sandboxes.yaml`: the namespace (Pod Security `privileged`, kept on uninstall), the
    server's Role there and a read-only ClusterRole (runtime classes, namespaces), the
    NetworkPolicy, and a MutatingAdmissionPolicy giving each sandbox container Docker's limits from
    `config.toml` (seccomp `RuntimeDefault`, `no_new_privileges`, `drop_capabilities`) and
    `SANDBOX_DISK_GB` as its `ephemeral-storage` limit; without that API (before Kubernetes 1.36)
    the chart refuses unless `sandboxes.hardening: optional`. Seen on kind: a sandbox with
    `NoNewPrivs: 1`, `Seccomp: 2`, `CapEff 0x800405fb` (no NET_RAW, NET_ADMIN, MKNOD, AUDIT_WRITE,
    SYS_ADMIN), limits cpu 1, memory 1Gi, ephemeral-storage 10Gi; labels `gen9-thread`,
    `gen9-user`, `opensandbox.io/id`. Processes: the kubelet's `podPidsLimit: 4096`
    (`deploy/kind.yaml`), a cluster's to set, as no pod field can.
  - [x] Verified on kind: `environments.mjs` and the rest of `make k8s-e2e` (U3's item); the
    sandboxes' egress and execd images the lock's digests.
- [ ] U7 Releases, as R1 decided: labels and `.github/release.yml`; the release workflow (images,
  chart, Compose bundle, SBOMs, attestations, a draft then published); the `release` environment;
  the version in the API, the web app, the CLI and the images' labels; `SECURITY.md`'s supported
  versions; "Releasing" in docs/development.md; Scorecard's workflow. Verified with a pre-release
  from a branch.
  - Read 2026-10-03, Helm's "Use OCI-based registries" (helm-www `docs/topics/registries.mdx`):
    `helm push <chart>.tgz oci://<registry>/<path>`; a provenance file (`.prov`) beside the
    `.tgz` is pushed with it as a layer of its own; Sigstore signing through the `helm-sigstore`
    plugin. Since Gen9 has eight charts plus the library, the charts go up as eight OCI artifacts,
    each attested by digest as the images are (U1) rather than with GPG provenance: to confirm
    against `actions/attest` for a non-image subject when U7 starts.
- [ ] U8 The owner's: the first release tag; GHCR packages public; the `release` environment's
  reviewer; `v*` tags creatable only by them (the ruleset's creation rule); registering at
  bestpractices.dev if they want the badge.
- [ ] Z1 Docs in step (operations.md, development.md, README, each stack's README, e2e/README,
  gen9-learn); `make e2e` against Docker and kind from published images.

## Surprises & Discoveries

- Gen9's sandboxes on Kubernetes, three things Docker gave for free: a MutatingAdmissionPolicy's
  apply configuration "may not mutate atomic arrays, maps or structs:
  .spec.containers[0].securityContext.capabilities.drop" (so it is a JSON patch, the container
  found by `indexOf`), and the API server takes a changed policy a few seconds late; Kubernetes
  runs a container without seccomp unless asked (`Seccomp: 0` until `RuntimeDefault`), where
  Docker applies its own filter; and no pod field limits processes, which is the kubelet's
  `podPidsLimit`. OpenSandbox's server also watches its snapshot CRDs, which Gen9 doesn't install
  ("Informer watch error: (403) … sandboxsnapshots", "sandboxesnapshots.sandbox.fast.io"), with a
  back-off: 33 warnings in 10 minutes, noise in its log, for upstream to quiet.
- The first `environments.mjs` on kind hung on its first command: OpenSandbox lists a chat's
  sandboxes with its own fast-sandbox kinds alongside, and a refused read of those (403) fails the
  whole listing ("List sandboxes failed: HTTP 503 … Fsb Sandbox CRs are unavailable"); allowed to
  read them, the server gets 404, which it takes for an empty list (`cr_reader.py`). Then the
  memory check: a command past a sandbox's memory took the whole sandbox down, where Docker kills
  that process alone. On cgroup v2 the kubelet sets `memory.oom.group` unless
  `singleProcessOOMKill: true` ("processes in the container to be OOM killed individually",
  [KubeletConfiguration](https://kubernetes.io/docs/reference/config-api/kubelet-config.v1beta1/)).
  An AI summary of that page gave `podPidsLimit` a default of 4096 and swap `LimitedSwap`; the
  page itself says -1 and `NoSwap`: read from the source, not a summary.

- The first install on kind showed two ways the template read Compose wrong. A `command` written
  as a string is split into words by Compose, not run by a shell: as `sh -c` it gave Redis
  `/bin/sh: 0: Illegal option --` and MinIO a command that exited. And `${X:-default}` takes X
  from the stack's `.env` before the default: Langfuse's own file gives its secrets that way
  (`${REDIS_AUTH:-myredissecret}`, `${POSTGRES_PASSWORD:-postgres}`, nested in `DATABASE_URL`),
  and `make setup` writes all of them, so on the cluster they took the defaults (32 of Langfuse's
  `.env` keys, 7 of the models', 9 of Keycloak's are such variables). Now the template splits a
  string command as Compose does, and `make k8s-up` gives each chart the names of its `.env`'s keys
  (`fromEnv`, never the values), so such a variable comes from the Secret, as Compose takes it.
  Langfuse's Postgres had already initialized with the default password, which it keeps:
  its namespace was deleted and installed again.
- Then the router: `DATABASE_URL: postgresql://litellm:${POSTGRES_PASSWORD}@postgres:5432/litellm`
  uses the plain `${X}` form, which the template didn't read, so LiteLLM got the text as written
  ("Database migration failed", "Prisma Client … Could not connect to the query engine", "Application
  startup failed"). Compose reads `${X}` and `$X` from `.env` or as empty, and keeps `$$` as a
  literal `$`, as Kubernetes does: the template does the same now. And a failing startup probe
  killed it into a crash loop: Docker marks a container unhealthy and never kills it for that, so
  a Compose health check is now a readiness probe at its interval, and while starting a startup
  probe at its `start_interval` that gives up only after a day. LiteLLM's `platform: linux/amd64`
  became a node selector (`kubernetes.io/arch: amd64`), as Docker would run that image only
  emulated elsewhere.
- gen9-agent's migrations, a pre-install hook, failed: "failed to resolve host 'gen9-postgres'".
  Helm creates a release's resources only after its pre hooks, and the name `gen9-postgres` in
  gen9-agent's namespace is one of them. The names a stack calls are now pre-install and
  pre-upgrade hooks themselves, weight -10, created before any other hook and kept
  (`before-hook-creation`). Running the migrations as the API's init container instead was
  rejected: `migrate.py` is "the only process that holds the owner's password", and the API and
  the worker would race on Alembic, which takes no lock. (Since replaced: no Service per name,
  DNS search domains instead, which a hook's pod has as well; the next entries and the Decision
  Log.)
- `models.mjs` failed on kind, once: over budget, the card said why but not when the limit resets.
  gen9-agent reads the reset time from gen9-models' admin API at `gen9-models-admin`, a second
  name gen9-models gives on its network; the chart made a name in the caller's namespace only for
  each stack's own (`gen9-models`), so `gen9-models-admin` didn't resolve ("Name or service not
  known" from the API's pod) and the agent left the time out, as it does when the API can't be
  read. Three stacks give a second name: gen9-keycloak `gen9-mailpit`, gen9-langfuse
  `gen9-langfuse-media` (the worker's trace media), gen9-models `gen9-models-admin` (the
  budget, a person's usage in their export). The caller's chart can't know another stack's names
  without reading that stack's files. A pod with that stack's namespace among its DNS search
  domains (`dnsConfig.searches`) resolved all of them, its own namespace still first, and an
  unknown name still failed (explore/deploy/NOTES.md, U3, "Another stack's names").
- Three checks start a one-off container with `docker compose run`: `search.mjs` (Temporal's CLI,
  a profiled service, to trigger the reindex Schedule), `context.mjs` and `fairness.mjs` (a second
  gen9-agent worker with one setting changed, `-d --name`, then `inspect`, `exec -i`, `rm -f`).
  `e2e/k8s/docker` refused them, and `search.mjs` failed its reindex step on kind. It now renders
  the service from its stack's chart with the release's own values (`helm get values`) as a Job,
  and runs that pod once, labelled `gen9.run` and not the service's name, so the worker's
  ReplicaSet doesn't adopt it: Temporal's `cli` answered `SERVING` and passed an exit status 3
  through; the second worker was healthy in about 6 s with `CONTEXT_BUDGET_TOKENS=12000` and the
  worker's search domains; `rm -f` of a missing one failed as Docker's does.
- `stop.mjs` failed on kind: it runs `make stop-agents` and `make resume-agents`, whose recipes
  are Docker's (`docker compose exec`, `stop`), and an operator on a cluster had no such stop at
  all. `make k8s-stop-agents` and `make k8s-resume-agents` (`scripts/k8s.sh`) now do the same in
  the cluster: `gen9-agent-stop` in the worker, then its Deployment scaled to 0; back to 1, then
  `--resume`. Tried on kind: "stopped: 0 runs, 0 scheduled tasks paused", the worker gone, both
  audit events; meanwhile `make k8s-diff` exited 2 naming the worker's replicas, and `make
  k8s-up` started it again, as `make up` does on Docker. `stop.mjs` runs the `k8s-` targets when
  `make k8s-e2e` sets `E2E_SHAPE=kubernetes`.
- `audit.mjs` failed on kind: "14 records, 11 lines", the three missing all `account.sweep`. The
  worker's sweep of accounts deleted in Keycloak added its audit rows without the `audit {…}` log
  line every other record has (docs/logging.md: the stream an operator sends elsewhere), and the
  check read only the API's log; a sweep inside the check fails it on Docker too, which the
  timing had hidden. Fixed in PR #67 (from `main`): the worker logs each line; on kind the sweep
  triggered by hand wrote three rows and three lines.
- On k3d (k3s v1.37.1), gen9-postgres's `extensions` Job failed all four tries: "connection to
  server at "postgres" (10.43.42.7), port 5432 failed: Connection refused", Postgres up and
  listening throughout. A new pod in that namespace reached Postgres on its second try, half a
  second after starting: k3s's network policy controller (kube-router) refuses a brand-new pod
  until its rules know the pod's address (kube-router#873, its rules lagging a pod's first traffic,
  reported in 2020 and still discussed by its maintainers in 2026),
  and each retry of a Job with `restartPolicy: Never` is a new pod. Kubernetes' "Network Policies",
  "Pod lifecycle": "a newly created pod may have no network connectivity at all when it is first
  started … pods must be resilient". kind's kindnet never showed it, nor Docker. The library's
  Jobs now restart in the same pod (`OnFailure`); then every stack installed on k3d.
- `models.mjs` failed on k3d: Langfuse had the call under `openai/gpt-6-luna` but no cost. The
  fresh Langfuse held only the 87 prices its migrations bring (none for gpt-5 or later): its
  worker loads the newest ones once, at start (Langfuse 4.48.0, `worker/src/initialize.ts`, no
  retry), and had started before the web's migrations made the tables: "Error upserting default
  model prices … relation "models" does not exist". Langfuse's own Compose file and Helm chart
  (langfuse-2.1.3) start the worker after Postgres alone, so a fresh install on Docker can meet it
  too. gen9-langfuse's `compose.override.yaml` now has `migrated`, a one-shot from the worker's
  image that waits for the web's health (served only once migrated), which the worker waits for:
  `depends_on` on Docker, its init container on Kubernetes. A fresh Langfuse on k3d: the one-shot
  waited, then "Finished upserting default model prices in 2113ms", 185 prices, gpt-6 among them;
  on Docker, a fresh throwaway project of gen9-langfuse (its own volumes): `migrated` waited, exited
  0, and the worker, started 20 s after the web, "Finished upserting default model prices in 2102ms".
- Not a difference between the shapes, kept for a follow-up: `runs.mjs`'s "a task with steps"
  failed once on kind and passed when run again. The failing run took the long way (13 tools
  live, "Used the research brief skill"; "Used 6 tools and a plan" when done; "Made a plan" after
  a reload, no tool counted), the passing one a short way (1 tool live, "Used 2 tools and a plan",
  the same after the reload). Not a race on saving: gen9-agent's writer stores the events in
  order and flushes them before marking the run a success (`runs/executor.py`, `_Writer`). Which
  steps the reloaded chat counts after a skill or subagent is to be looked into, on Docker too.
- `retry.mjs` failed on kind: with the router stopped, the card "Gen9 couldn't finish" didn't come
  within its 120 s. A stopped router on Docker is refused at once (its name gone); on kind the
  shim scales its Deployment to 0, the Service stays, and kube-proxy (iptables mode) answers a
  connection to it with an ICMP refusal, which kindnet's network policy (kube-network-policies
  v1.1.2) drops when the caller's namespace has an ingress policy: from gen9-agent the connect
  timed out after 15 s, from a namespace without a policy it was refused in 0.00 s. Its chain
  accepts established and related packets only of connections its queue accepted, and queues
  only new ones (`pkg/dataplane/controller.go`); NetworkPolicy can't allow ICMP. That exposed a
  bug in gen9-agent, not the deployment: a chat call had no timeout at all (langchain-openai
  gives the OpenAI client `timeout=None`, which replaces the HTTP client's `connect=10`), and
  waited 134 s for the kernel to give up. Fixed in PR #65 (from `main`): 10.1 s on kind.
- CI's gen9-ui job failed on every pull request from 2026-10-03: npm audit took in braces'
  GHSA-vfj7-8cjw-p6xm (published 2026-09-18, no fixed release), which only build tools reach.
  PR #66 (from `main`) accepts an npm advisory with no fix only with its reason, for one version
  of its package (`scripts/npm-audit.json`), as Grype's list does for images.

- `make e2e` didn't read `images.env`: `context.mjs` and `fairness.mjs` start a second worker with
  `docker compose run`, which, without the lock's variables, would build `gen9-agent:dev` here
  instead of running the lock's image. `make e2e` now reads it as `make up` does.
- `background.mjs` failed once against the lock's deployment ("checked it: false; said:
  kumquat-1e…": the chat model answered the code word itself instead of saying it had started the
  task) and passed on the next run: the model's, not the deployment's (the check was loosened for
  the same reason before, a6fb412).

- The first `make e2e` from a lock failed `environments.mjs` ("printed 42: false; 0 container(s)"):
  OpenSandbox's server read `/etc/opensandbox/config.toml` ("Loaded configuration from
  /etc/opensandbox/config.toml"), not the copy with the lock's execd and egress images, because
  the image starts it with `--config` (gen9-sandbox/Dockerfile's CMD), which wins over
  `SANDBOX_CONFIG_PATH`; it then asked Docker for the local tag `gen9-sandbox-egress:release-1.1.0`,
  removed for the test ("No such image"). `launch.py` now points `--config` at the copy.

- Docker's bake workflow builds from a git context pinned by checksum
  (`https://github.com/coast-guide/gen9.git?ref=…&checksum=…&fetch-by-commit=true`), which needs a
  Dockerfile frontend that knows BuildKit's `source.git.checksum`: the three Dockerfiles pinned to
  `# syntax=docker/dockerfile:1.7` (gen9-postgres, the egress and execd images) failed on both
  platforms with "failed to resolve dockerfile: unknown API capability source.git.checksum", while
  those on `docker/dockerfile:1` (1.27.1) built. 1.7 was there as the floor for `ADD --checksum`;
  all five now say `docker/dockerfile:1`. Local builds never showed it: they send the folder, not
  a git URL.

- OpenSandbox's Kubernetes runtime works with Gen9's egress and execd images unchanged (a command
  ran, the allowed host answered, an undeclared host and the metadata address were blocked), but
  its pod is looser than Gen9's Docker sandboxes: a privileged init container, only `NET_ADMIN`
  dropped, root, 2 GiB, no NetworkPolicy; and the chart doesn't create the sandboxes' namespace
  (NOTES.md, R2d).

- This machine has Docker 29.8.1 (Docker Desktop: a VM of 20 CPUs and 17.6 GiB) and Compose
  v5.5.1, and had no Kubernetes tooling: `which kind k3d helm kubectl` printed nothing. R2c
  installed them from their release pages, checksums verified, into `~/.local/bin`. Docker Desktop
  shares only the home folder with its VM: a probe under `/tmp` fails with "mounts denied", so
  probes run in `~/.cache/gen9-probes`.
- `docker compose publish` refuses a project "with service(s) containing bind mounts", "containing
  only a `build` section", or including local files with `include`
  ([Docker docs](https://docs.docker.com/compose/how-tos/oci-artifact/)). Gen9's Compose files
  bind-mount configuration 16 times in six stacks (`grep -nE '^\s+- \.{1,2}/' gen9-*/compose.yaml
  gen9-langfuse/*.y*ml`): Keycloak's realm and config, Postgres's initdb and scripts, ClickHouse's
  disk settings, OpenSandbox's `config.toml`, Temporal's initdb, scripts and dynamic config, the
  router's config, SearXNG's settings, the models' scripts and admin page; and OpenSandbox's
  server mounts the Docker socket. In Compose v5.5.1 they are not refused after all: Compose asks
  to publish "only the bind mount declarations … (not content)", and pulled back they point at an
  empty folder; inline `configs:` travel (explore/deploy/NOTES.md, R2a).
- Compose Bridge's default transformation makes manifests kind's API server refuses
  (`restartPolicy: "unless-stopped"`; `"yes"` turned into a boolean), crashes on Gen9's
  healthchecks, and writes secrets into the Deployments as plain values (NOTES.md, R2b).
- Neither local cluster routes HTTP to a Gateway out of the box: kind has no Gateway API; k3s
  installs its CRDs (v1.6.1) and Traefik 3.7.13 with only the Ingress provider on (NOTES.md, R2c).
- Drift by hand escapes the obvious checks: `helm diff upgrade` without `--three-way-merge` says
  nothing changed, because it compares with the release Helm stored; Compose's config hash misses
  `docker update` and files changed inside a container (NOTES.md, R2e and R2a).
- Helm 4 refuses a plugin it can't verify: helm-diff installs from its release tarball with the
  `.prov` and the maintainer's key, not from the git URL (NOTES.md, R2e).
- Langfuse's own chart (langfuse-k8s 2.1.3) brings different parts than its Compose file: Postgres
  from groundhog2k's chart, Valkey, and SeaweedFS for S3 where Compose runs MinIO (its
  `Chart.yaml`). Upstream charts would make the Kubernetes shape differ from the Docker one.
- On Kubernetes, nodes run containerd with no Docker socket, so OpenSandbox's Docker runtime has
  nothing to talk to there: its Kubernetes runtime (a controller with CRDs `BatchSandbox`, `Pool`,
  `SandboxSnapshot`, and the server's `[kubernetes]` settings) is the only way
  ([OpenSandbox, deployment](https://github.com/opensandbox-group/OpenSandbox/blob/release-1.1.0/docs/deployment/index.md),
  [Helm deployment](https://github.com/opensandbox-group/OpenSandbox/blob/release-1.1.0/manifests/HELM-DEPLOYMENT.md)).

## Decision Log

Research as of 2026-10-01. Versions from each project's latest GitHub release that day
(`gh api repos/<repo>/releases/latest`): Helm v4.3.0, kind v0.33.0, k3d v5.9.0, k3s
v1.37.0+k3s1, Kubernetes v1.37.1, Kustomize v5.8.2, helm-diff v3.15.15, Argo CD v3.5.3, Flux
v2.9.6, cosign v3.1.3, docker/build-push-action v7.4.0, docker/bake-action v7.4.0,
docker/metadata-action v6.2.0, docker/setup-buildx-action v4.4.1, docker/login-action v4.6.0,
actions/attest v4.2.2, release-please v17.11.2 (action v5.0.0), git-cliff v2.14.2,
anchore/sbom-action v0.24.2, ossf/scorecard-action v2.4.4, helmfile v1.8.1, kubeconform v0.8.0,
Gateway API v1.6.2, cert-manager v1.21.2, Traefik v3.7.13, Envoy Gateway v1.9.2, CloudNativePG
v1.30.1, Compose v5.5.1, Kompose v1.38.0; OpenSandbox release-1.1.0 (release-1.1.1-rc.1 is a
candidate); Temporal's chart temporal-1.7.0, Langfuse's langfuse-2.1.3.

- Decision: Kubernetes packaging is a Helm chart, published to GHCR as an OCI artifact and
  installed by digest; whether it is written by hand or generated from the Compose files is
  R2b's to settle. Rationale: Helm's docs: "It is recommended to use container registries with OCI
  support to store and share chart packages"
  ([Helm, registries](https://helm.sh/docs/topics/registries/)); Helm 4 installs "charts by digest
  for better supply chain security", defaults to server-side apply for new releases, and "v2 charts
  continue to work unchanged" ([Helm overview](https://helm.sh/docs/overview/)); the parts Gen9
  runs ship Helm charts upstream (Temporal, Langfuse, LiteLLM, OpenSandbox's controller); a values
  file with a JSON schema is the configuration surface item 4 asks for, which Kustomize has no
  equivalent of.
- Decision: the Kubernetes shape runs the same images as the Docker shape, by digest, and the same
  configuration files, rather than upstream charts that swap parts (refined by R3, "Third-party
  guides": an upstream chart is used where it runs the same images). Rationale: the owner's core
  concern ("shouldn't be any difference"); Langfuse's chart swaps MinIO for SeaweedFS (Surprises).
  An upstream chart can still be used where it runs the same images (to check per part in U3).
- Decision: HTTP enters through the Gateway API (HTTPRoute), with a plain Ingress as a setting.
  Rationale: Kubernetes retired Ingress NGINX (best-effort maintenance until March 2026, then "no
  further releases, no bugfixes") and recommends the Gateway API
  ([Kubernetes blog](https://kubernetes.io/blog/2025/11/11/ingress-nginx-retirement/)); the Gateway
  API is v1.6.2; any conformant controller works (k3s ships Traefik, which implements it).
- Decision: sandboxes on Kubernetes use OpenSandbox's Kubernetes runtime (Surprises). The sandbox
  runtime is one of the settings: Docker for the Docker shape, Kubernetes for the cluster shape.
- Decision: portability is proven on two distributions, kind (Kubernetes' own, used for its
  conformance tests) and k3d (k3s, the small distribution people run on VMs), with the same chart
  and the same values; then on any cluster the owner has.
- Decision: images go to GHCR, built with bake for linux/amd64 and linux/arm64, attested with
  `actions/attest@v4` (provenance and SBOM, `push-to-registry`), verified with
  `gh attestation verify oci://…`. Rationale: GitHub's docs name `actions/attest@v4` with
  `id-token`, `attestations` and `packages` write
  ([artifact attestations](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations)).
  The repository already has immutable releases on (`gh api repos/coast-guide/gen9/immutable-releases`:
  `"enabled":true`) and a ruleset blocking deletion and updates of `refs/tags/v*`
  ("Protect release tags"). Immutability "will only apply to future releases"; tag and assets
  "cannot be changed" once published
  ([GitHub, immutable releases](https://docs.github.com/en/code-security/supply-chain-security/understanding-your-software-supply-chain/immutable-releases)).
- Decision (superseded by "Release management (R1)" below): release notes. Gen9's commit subjects are prose (AGENTS.md, "Rules"), and
  release-please "assumes you are using Conventional Commit messages"
  ([release-please](https://github.com/googleapis/release-please)); GitHub's generated notes are
  built from merged pull requests and grouped by labels in `.github/release.yml`
  ([GitHub, generated release notes](https://docs.github.com/en/repositories/releasing-projects-on-github/automatically-generated-release-notes)),
  which fits prose subjects. SemVer 2.0.0 is current; "Major version zero (0.y.z) is for initial
  development. Anything MAY change at any time" ([semver.org](https://semver.org/)).

- Decision: the chart is written by hand, not generated from the Compose files. Rationale: R2b,
  Compose Bridge's default transformation makes manifests the API server refuses and puts secrets in
  them; templates of our own would be a second chart in Go templates over Compose's model, with
  less to check them by than Helm has (`helm lint`, `values.schema.json`, `helm template` into
  kubeconform). A check compares the two shapes instead (U3): the same images by digest, the same
  settings keys, the same ports and the same configuration files.
- Decision: the drift check on Kubernetes is `helm diff upgrade --three-way-merge
  --detailed-exitcode` (exit 2 names each object), and putting things back is
  `helm upgrade --server-side=true --force-conflicts`. Rationale: R2e; the default two-way diff
  missed a change by hand, a plain upgrade failed on the field managers the change left. A GitOps
  controller (Flux v2.9.6, Argo CD v3.5.3) reconciles continuously and stays an option a cluster's
  owner can point at the same chart; Gen9 doesn't require one.
- Decision: the drift check on Docker compares, per service, Compose's config hash
  (`docker compose config --hash '*'` against the `com.docker.compose.config-hash` label), the image
  digest running against the lock, the settings `docker update` changes, and containers Compose
  doesn't declare; containers whose files must not change run `read_only` (U2, U4). Rationale: R2a,
  the hash alone missed `docker update` and an edited file.
- Decision: how HTTP gets in is a setting with three answers: an HTTPRoute on a Gateway the cluster
  has (named in the settings), an Ingress of a class it has, or neither. Rationale: R2c, no cluster
  routes to a Gateway out of the box, and only k3s routes Ingress.

- Decision: images are built by Docker's reusable bake workflow
  (`docker/github-builder/.github/workflows/bake.yml`, v1.17.0, pinned by commit), one call per
  image in a matrix, from `docker-bake.hcl` at the root. Rationale: Docker's docs now point to it
  instead of "maintaining a custom matrix and merge job": it splits the platforms across native
  runners (`ubuntu-24.04-arm` for arm64, free on public repositories), pushes by digest, merges the
  manifest, and signs BuildKit's SLSA provenance (mode `max` on a public repository) with the
  workflow's identity; its own actions are pinned by commit, as this repository requires
  ([Docker, multi-platform](https://docs.docker.com/build/ci/github-actions/multi-platform/),
  [docker/github-builder](https://github.com/docker/github-builder),
  [GitHub-hosted runners](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)).
  One call builds one target (`target`, its `meta-images`), so 7 calls; Docker's maintainer of
  these actions builds his images to GHCR the same way (`crazy-max/docker-fail2ban`,
  `.github/workflows/build.yml`). GitHub's own attestation (`actions/attest@v4`) is added after it,
  for `gh attestation verify`. Every base image Gen9 pins is a multi-platform index with amd64 and
  arm64 (`docker buildx imagetools inspect --raw`), and gen9-postgres already picks its extension
  by `TARGETARCH`.
- Decision: the lock of digests is made by the build, not committed. The digests exist only after
  CI builds, and builds aren't bit-for-bit reproducible, so git can't hold them before the build
  without a bot committing after it (Flux's image automation pattern). Instead the workflow writes
  the lock as an artifact of its run, and a release attaches it and bakes it into the chart and the
  Compose bundle (U7): the release, immutable, declares what runs, and git declares how it was
  built. This changes acceptance item `images.lock` into `images.lock-released`.
- Decision: `docker-bake.hcl` names each image's context, which the Compose files name too
  (`build:`); bake can't read Gen9's Compose files in CI, where no `.env` exists ("env file .env
  not found"). `scripts/check-images.py`, run by `make config` and so by CI, fails when they
  differ.

- Decision (for U3, refining "the chart is written by hand"): each chart's Kubernetes structure
  (workloads, storage, probes, Services) is written by hand, but what drifts most is read from the
  stack's `compose.yaml`, linked into the chart: third-party images and each container's
  environment, through one helper in the library chart. `${X:?…}` (a secret from `.env`) becomes a
  reference to the Secret `env`; `${X:-default}` the setting `X` from the settings file, else its
  default; a secret inside a longer value Kubernetes' `$(X)`. So the settings have one vocabulary
  in both shapes: `X` in a stack's `.env` on Docker, `settings.X` on Kubernetes. Rationale: Helm
  parses Gen9's Compose files whole, anchors and merges included (explore/deploy/NOTES.md, U3, "A
  chart that reads its stack's compose.yaml"); unlike Compose Bridge, the structure stays ours and
  checked by `helm lint` and kubeconform.
- Decision (for U3): each stack is a Helm chart of its own, installed as its own release in its own
  namespace `gen9-<stack>`, in `make`'s stack order, as each is its own Compose project today. A
  stack reaches another under the same name as on Docker (`gen9-keycloak`, `gen9-agent`,
  `gen9-models`…) through an ExternalName Service in its own namespace, only for the stacks it
  uses (the Makefile's `USES_*`); each stack's NetworkPolicy lets in only the namespaces that use
  it, as the `gen9-<stack>` networks do; names inside a stack (`postgres`, `valkey`) stay its own.
  Rationale: AGENTS.md, "Decoupled stacks"; the apps keep their settings unchanged; probed on kind
  (explore/deploy/NOTES.md, U3: an ExternalName to another namespace's Service answered, and
  kindnet enforced the policy). One file of settings is given to every release, each chart reading
  its own part and the shared one (U5). Each chart sits in its stack's folder (`gen9-<stack>/chart/`)
  and links to the configuration files Compose mounts, which Helm reads through the link and
  packages as files (explore/deploy/NOTES.md, U3, "A chart in the stack's folder").
- Decision: the lock's images go in `images.env` at the top of the repository, which `make up`
  reads into every stack's environment, rather than into each stack's `.env`. Rationale: one file
  says which images the whole install runs, a drift check (U4) reads one file, and `make setup`,
  which writes the `.env` files, stays out of it; `IMAGES=local` removes it.
- Decision: the Docker shape is the release's source (its git tag, or the source archive GitHub
  attaches to every release) and its lock: `make up IMAGES=<lock>`. Compose's OCI artifacts
  (`docker compose -f oci://…`) are not used. Rationale: R2a, a published Compose app carries a
  bind mount's declaration without its files, and Gen9 mounts 16; it would be 8 artifacts, one per
  stack, joined by networks only `make up` creates; and setup, backup, restore, doctor and wipe
  live in the repository's scripts. So the host needs Docker, `make` and the source; the images
  come by digest; what runs is the tag plus the lock. Compose pulls rather than builds when a
  service has both `image` and `build` ("pulling the image is the default behavior",
  [Compose, services](https://docs.docker.com/reference/compose-file/services/)); `--no-build`
  makes sure. An override file with `build: !reset null`
  ([Compose, merge](https://docs.docker.com/reference/compose-file/merge/)) would do the same with
  a second file per stack to keep in step; a variable in the one file is simpler. OpenSandbox
  reads `execd_image` and the egress image only from its TOML (its `config.py` overrides only the
  API key, the database DSN and the secure-access keys from the environment), so `launch.py`
  writes the server a copy of `config.toml` with them.

- Decision (U3): one template for every stack, `gen9.stack` in the library chart, which renders a
  stack from its `compose.yaml` and a few values per service: the workload `kind` (Deployment,
  StatefulSet, Job, Init, none), `ports`, `storage`, `resources`. Compose's one-shot services map
  to Kubernetes by what waits for them: a `service_completed_successfully` inside the same stack
  becomes an init container of the waiting service (`kind: Init`), since a post-install hook would
  wait for that service and a pre-install hook would run before the stack's own database exists
  (Temporal's schema); a one-shot that waits for a healthy service becomes a post-install and
  post-upgrade hook Job (Postgres's extensions and roles, Keycloak's configure, the router's keys);
  migrations other services wait for, against another stack's database, a pre-install and
  pre-upgrade hook (gen9-agent's migrate). Helm runs post hooks only once everything else is ready
  under `--wait`, and waits for a hook Job to finish ([Helm, chart hooks](https://helm.sh/docs/topics/charts_hooks/)).
  A Compose service with no entry fails the render, unless it is in a Compose profile (left out,
  as `make up` leaves it out).
- Decision (U3, U5): every chart validates its values against one JSON schema
  (`deploy/helm/values.schema.json`, linked into each chart): unknown keys, a misspelt setting or a
  size Kubernetes can't read fail `helm install` with the path. One settings file serves every
  release: `settings.X` for all stacks, `<stack>.settings.X` and `<stack>.services.…` for one, so
  a change for Keycloak's Postgres doesn't reach Temporal's.
- Decision (U3): `make e2e` runs against Kubernetes unchanged, through `e2e/k8s/docker`, a `docker`
  first on PATH that does to `gen9-<stack>-<service>-1`'s pod what the checks ask of the container
  (exec, logs, restart, stop and start, health). Rationale: 25 of the 55 checks reach into a
  container by `docker`; rewriting them for two shapes would let them drift apart.
- Decision (U8): GHCR packages are private when first published ("When you first publish a package,
  the default visibility is private", [GitHub, Container registry](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry));
  making them public, so any machine pulls without a login, is the owner's.

- Decision (U6): sandboxes on Kubernetes are `kubernetes-sigs/agent-sandbox`'s `Sandbox`, driven
  by OpenSandbox's server (`workload_provider = "agent-sandbox"`), not OpenSandbox's own
  `BatchSandbox`. Rationale: both ran Gen9's images the same on kind (R2d, U6), with the same
  egress sidecar code; agent-sandbox is a SIG Apps API at v1, its controller published to
  `registry.k8s.io` and its install one release file with a digest GitHub records, where
  OpenSandbox's 1.1.0 CRDs and controller are published nowhere (only in the source at the tag)
  and its controller holds a ClusterRole over pods everywhere. The server gets rights only in the
  sandboxes' namespace (upstream's server chart has a ClusterRole that creates pods and Secrets in
  any namespace, and none on `agents.x-k8s.io`).
- Decision (U6): keep OpenSandbox's `disable_ipv6 = true`, so each sandbox's execd init container
  runs privileged for the moment it takes to turn IPv6 off in the pod's network and copy execd;
  the sandbox's own container stays unprivileged with `NET_ADMIN` dropped. Rationale: OpenSandbox
  says its IPv6 egress is incomplete on Kubernetes; the sysctl that would do it without privilege
  (`net.ipv6.conf.all.disable_ipv6`) is not one Kubernetes deems safe, so a kubelet would have to
  allow it, which no portable chart can count on. The sandboxes' namespace is labelled for Pod
  Security Admission's `privileged` level, and only there.

### Release management (R1)

The list of what a proper release needs, from today's sources, and what Gen9 does for each. What
the repository already has: immutable releases on, `v*` tags protected from deletion and updates,
`SECURITY.md` with private reporting, Dependabot for the Actions, CodeQL, secret scanning with
push protection, Actions pinned by SHA, `contents: read` by default in `checks.yml`.

1. **One version for all of Gen9.** SemVer 2.0.0, `vX.Y.Z`, pre-releases `vX.Y.Z-rc.N`; one
   version covers the images, the chart, the Compose bundle, the CLI and the API, because they are
   tested together. Start at 0.1.0, which `gen9-agent`, `gen9-cli` and `gen9-ui` already declare:
   "Major version zero (0.y.z) is for initial development" ([semver.org](https://semver.org/)).
   The public API SemVer speaks of: the agent's HTTP API, the CLI, the settings (chart values,
   Compose settings) and the stored data's migrations.
2. **Immutable releases, drafted first.** Once published, a release's "assets can't be added,
   modified, or deleted" and its tag can't move; GitHub recommends creating it as a draft,
   attaching every asset, then publishing
   ([managing releases](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository),
   [immutable releases, generally available](https://github.blog/changelog/2025-10-28-immutable-releases-are-now-generally-available/)).
   Publishing makes a release attestation; anyone checks with `gh release verify <tag>` and
   `gh release verify-asset <tag> <file>`
   ([verifying a release](https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/secure-your-dependencies/verifying-the-integrity-of-a-release)).
   Those commands came in gh 2.75 to 2.81 (gh v2.102.0 is current); this machine's apt build is
   2.45.0 and lacks them, so the docs name the version needed.
3. **Release notes on the GitHub Release, no CHANGELOG.md.** GitHub generates them from the pull
   requests merged since the last release, grouped by labels in `.github/release.yml`. Gen9's
   pull request titles are already sentences for people, which is what a changelog needs
   ("Changelogs are for humans … Using commit log diffs as changelogs is a bad idea",
   [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/)); its groups (Added, Changed,
   Deprecated, Removed, Fixed, Security) become the labels. Each release adds, by hand, an
   "Upgrading" section when a setting or a migration needs the reader. One place for it: the
   release. release-please (v17.11.2) is not used: it "assumes you are using Conventional Commit
   messages", which AGENTS.md's commit rules don't produce.
4. **The release workflow** (`.github/workflows/release.yml`, on a `v*` tag): build the 7 images
   once with bake for linux/amd64 and linux/arm64 and push them by digest to GHCR; attest
   provenance and SBOM for each (`actions/attest@v4`, `push-to-registry`); package the chart with
   the version and the digests, push it to `oci://ghcr.io/coast-guide/charts/gen9`, attest it by
   digest ("invoke the action with the `subject-name` and `subject-digest` inputs",
   [actions/attest](https://github.com/actions/attest)); write the Compose bundle and the SBOMs as
   assets, with the attestation bundles as `*.sigstore.json` (what Scorecard's Signed-Releases
   looks for); create the draft with generated notes, upload, publish. The publishing job runs in
   a `release` environment with the owner as required reviewer.
5. **What runs says what it is.** The version and the commit in the images' OCI labels
   (`org.opencontainers.image.version`, `revision`, `source`), in the API (a version endpoint),
   the web app and `gen9 --version`, so a person and the drift check can see which release runs.
6. **Upgrades.** The migrations run themselves (gen9-agent's migrate job, Temporal's schema jobs,
   Keycloak's realm), as they do with `make up`; `docs/operations.md`, "Upgrade", covers going from
   one release to the next in both shapes; a check upgrades the previous release to the new one on
   kind before a release (U7).
7. **Support.** While 0.y: only the latest release gets fixes, as a patch release; `SECURITY.md`
   says so and how long a reporter waits (it already says 7 days, 90 days to disclosure).
8. **Scorecard.** `ossf/scorecard-action` v2.4.4 on `main` weekly and on push, results to code
   scanning; then fix what it finds. Its checks
   ([docs/checks.md](https://github.com/ossf/scorecard/blob/main/docs/checks.md)) Gen9 would meet
   after U7: Branch-Protection, CI-Tests, Code-Review, Dangerous-Workflow,
   Dependency-Update-Tool, License, Pinned-Dependencies, SAST, SBOM, Security-Policy,
   Signed-Releases, Token-Permissions, Packaging (the GHCR packages). The OpenSSF Best Practices
   badge (CII-Best-Practices) needs the owner to register the project.
9. **Cadence.** A release when `main` is green and `make e2e` passed against both shapes from the
   release candidate's images; no backport branches while 0.y.

### Third-party guides (R3)

Each part's own docs, read on 2026-10-01 for the version Gen9 pins (latest releases that day in
brackets). The rule for the chart that follows from them: Gen9 requires of a cluster only what
can't be avoided (OpenSandbox's CRDs and controller); every other part runs the same image as in
Docker, from an upstream chart where that chart runs the same images and adds no cluster-wide
prerequisite, else from Gen9's own templates; each data store can instead be an external service
(a setting), which is how a cluster owner brings an operator or a managed service.

| Part | What its own docs give for Kubernetes | Gen9's chart |
| --- | --- | --- |
| Keycloak 26.7.5 [26.8.0, out today] | The Keycloak Operator: OLM "the recommended way to install" it, or `kubectl apply -k …keycloak-k8s-resources/kubernetes?ref=<version>`; its Helm chart is "Experimental … a preview". It needs a database provided by the user, a TLS Secret and a hostname; a custom image "requires a high degree of trust"; realms through `KeycloakRealmImport`. Images: build optimized (`kc.sh build`), health on port 9000 | Own templates: the gen9-keycloak image with `start --optimized` and the realm import, as in Docker. The Operator needs cluster-wide CRDs and a second way to configure the same server; documented as the alternative |
| Temporal 1.32.0, UI 2.54.1 | Its Helm chart (temporal-1.7.0): "installs only the Temporal server components. You must provide persistence"; runs schema jobs (`manageSchema`); images `temporalio/server:1.32.0`, `admin-tools:1.32.0`, `ui:2.54.1`, Gen9's exact pins. Services "should run on hosts that are not accessible from the public internet" | Adopt the chart as a dependency, pointed at Gen9's Postgres |
| Langfuse 4.48.0 [4.49.0] | Docker Compose is for "Local use and testing", a "Single VM without high availability, scaling, or backups"; Kubernetes (Helm, langfuse-2.1.3) for production. From chart 2.0.0 its bundled ClickHouse needs "the ClickHouse Kubernetes Operator" (and cert-manager); it bundles SeaweedFS for S3 and recommends a managed blob store in production; each store can be external (`*.deploy: false`). Kubernetes 1.28 or newer | Adopt the chart for Langfuse's web and worker (the same images), every store external, pointed at Gen9's |
| LiteLLM v1.103.1 [v1.103.2] | Images `ghcr.io/berriai/litellm` ("pin a version tag"); charts `oci://ghcr.io/berriai/litellm-helm` (monolithic) and a componentized one; a migrations job, `DISABLE_SCHEMA_UPDATE=true` on the proxies; a production checklist. The monolithic chart (1.1.3) defaults to v1.85.1 and depends on Bitnami's PostgreSQL and Redis charts with `bitnamilegacy/*` images | Own templates: one Deployment, the migrations Job, Gen9's `config.yaml`; the chart's dependencies would vendor Bitnami's legacy charts |
| OpenSandbox 1.1.0 | Its charts (all-in-one `opensandbox` 1.1.0: CRDs, controller, server); `[secure_runtime]` with `k8s_runtime_class` (gVisor, Kata, Firecracker), and the server "will refuse to start if the runtime is unavailable" | Adopt its charts; Gen9's execd and egress images, Gen9's BatchSandbox template, the sandboxes' namespace and a NetworkPolicy (U6) |
| PostgreSQL 18 (Gen9's image with pgvector), 16 and 17 | The official image (init scripts in `docker-entrypoint-initdb.d`); for Kubernetes, CloudNativePG 1.30 (images `ghcr.io/cloudnative-pg/postgresql`, extensions through image volumes or its standard images) | StatefulSets of the same images; CloudNativePG or a managed Postgres as external |
| Valkey 9.1.2, Redis 7.4 | valkey-helm (official): `valkey` "Standalone / replication without operator", image `valkey/valkey` | Own small StatefulSets, or the `valkey` chart if its values take Gen9's settings (U3) |
| ClickHouse 26.8 | ClickHouse's own operator is `v1alpha1` and needs cert-manager; Altinity's operator (0.27.4) is the long-standing one | One StatefulSet of the official image, as in Docker; a ClickHouse cluster as external |
| MinIO (Chainguard's build) | `minio/minio` is archived: "THIS REPOSITORY IS NO LONGER MAINTAINED", pointing at AIStor; Langfuse's own Compose file still uses `cgr.dev/chainguard/minio` | U1b |
| SearXNG, Mailpit, Ollama, llama.cpp | Container images and Compose only; no Kubernetes guidance. Mailpit is an email testing tool; Ollama and llama.cpp have GPU image variants | Own Deployments; SMTP is a setting (Mailpit only when none is set); local models optional, GPUs a setting |

Sources: [Keycloak Operator installation](https://www.keycloak.org/operator/installation),
[basic deployment](https://www.keycloak.org/operator/basic-deployment),
[containers](https://www.keycloak.org/server/containers);
[Temporal, deployment](https://docs.temporal.io/self-hosted-guide/deployment),
[temporalio/helm-charts](https://github.com/temporalio/helm-charts);
[Langfuse, self-hosting](https://langfuse.com/self-hosting),
[Kubernetes (Helm)](https://langfuse.com/self-hosting/deployment/kubernetes-helm),
langfuse-k8s `charts/langfuse/Chart.yaml` and `values.yaml`;
[LiteLLM, deploy](https://docs.litellm.ai/docs/proxy/deploy), `helm/litellm-helm/Chart.yaml`;
OpenSandbox `docs/guides/secure-container.md` and `manifests/charts` at `release-1.1.0`;
[CloudNativePG](https://cloudnative-pg.io/docs/devel); [valkey-helm](https://github.com/valkey-io/valkey-helm);
[ClickHouse operator](https://github.com/ClickHouse/clickhouse-operator); the
[minio/minio README](https://github.com/minio/minio);
[SearXNG, Docker](https://docs.searxng.org/admin/installation-docker.html),
[Mailpit, Docker](https://mailpit.axllent.org/docs/install/docker/),
[Ollama, Docker](https://docs.ollama.com/docker),
[llama.cpp, Docker](https://github.com/ggml-org/llama.cpp/blob/master/docs/docker.md).

- Decision (for U3, replacing the ExternalName Services above): a stack reaches another's names
  through its pods' DNS search domains. A service that joins another stack's network `gen9-<x>`
  in its `compose.yaml` gets `gen9-<x>.svc.<cluster domain>` in `dnsConfig.searches`, after its
  own namespace. Rationale: it is what joining a network does on Docker, every name given there
  resolves, read from the same file, with nothing for a caller's chart to list of another stack's
  (an ExternalName per name missed `gen9-models-admin`, Surprises); per service, as on Docker
  (gen9-agent's `api` joins four stacks, its `worker` six, its `migrate` only gen9-postgres); a
  pre-install hook's pod has its search domains like any other, so the names no longer need to
  be hooks created first. Cost: a name with fewer than five dots (`ndots:5`, the cluster default)
  is tried against each search domain before as given, so the worker's lookups of outside hosts
  take up to six more misses each, answered from CoreDNS's cache once seen. Sources: Kubernetes'
  "DNS for Services and Pods" (`searches` "merged into the base search domain names", "up to 32
  search domains", stable since 1.28); probed on kind (explore/deploy/NOTES.md, U3).

## Outcomes & Retrospective

None yet.

## Context

Gen9 runs as eight Compose projects (`ALL_STACKS` in the `Makefile`: postgres, keycloak, langfuse,
temporal, models, sandbox, agent, ui), each its own folder with its own `.env` written by its
`init-env.sh` (`make setup`), joined by `gen9-<stack>` networks (docs/development.md, "How stacks
stay decoupled"). `make up` builds 7 images on the machine with local tags: `gen9-agent:dev`,
`gen9-ui:prod`, `gen9-keycloak:26.7.5`, `gen9-postgres:18-pgvector0.8.6-textsearch1.4.0`,
`gen9-sandbox:opensandbox-1.1.0`, `gen9-sandbox-egress:release-1.1.0`, `gen9-sandbox-execd:v1.1.0`.
The other images (Temporal, LiteLLM, Langfuse, ClickHouse, MinIO, Redis, Postgres, Valkey,
Mailpit, SearXNG, Ollama, llama.cpp) are pinned by digest in the Compose files. Configuration is
bind-mounted from each folder. OpenSandbox runs its Docker runtime: the server starts each
sandbox as sibling containers through the Docker socket (gen9-sandbox/README.md).

Nothing is published today: no tags, no releases, no packages, no Kubernetes files.
`.github/workflows/checks.yml` runs the checks on pull requests; Dependabot updates the Actions.

Terms: a *deployment shape* is Docker (Compose) or Kubernetes (Helm). The *lock* is the list of
Gen9's image digests a version runs. *Drift* is any difference between what the lock and the
settings declare and what runs.

## Plan of work

1. Research and probes (R1, R2): settle the open choices with evidence before building.
2. Artifacts (U1): images built once and attested; the lock in git.
3. Docker (U2), then Kubernetes (U3, U6), each verified with `make e2e` against it.
4. Drift (U4) and the configuration surface (U5) across both.
5. Releases (U7), then the owner's first tag (U8), then the docs (Z1).

## Validation

- `docker buildx bake --print` and the workflow's run: 7 images, 2 platforms each, attestations
  verified with `gh attestation verify oci://ghcr.io/coast-guide/gen9-<name>@<digest> -R coast-guide/gen9`.
- The Docker shape on this machine from the lock, nothing built: `make e2e` passes.
- The chart on kind and on k3d with the same values: `kubeconform -strict` on the render, the pods
  ready, `make e2e` passes against each.
- The digests running in each shape equal the lock.
- The drift command: 0 on a fresh deployment; non-zero, naming the service, after
  `docker update`, `kubectl edit` or `kubectl set image` by hand.
- A pre-release from a branch: a GitHub Release with notes and assets, `gh release verify`.

## Interfaces

To be fixed by R2 and U1 to U5; the expected shape: a bake file at the root; `make images`
(build locally) and the release workflow (build and push); the lock file; `make deploy-docker`,
`make deploy-k8s` and `make drift`; the chart under `deploy/`; one settings reference. As built:
`make up IMAGES=<lock>` and `make k8s-up IMAGES=<lock>`; `make diff` and `make k8s-diff`, `make
reset` and `make k8s-reset`; a chart per stack (`gen9-<stack>/chart`) on the library `deploy/helm/gen9-lib`.
