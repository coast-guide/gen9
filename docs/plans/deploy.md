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

- [ ] R0 This plan, its acceptance list, and AGENTS.md pointing at it.
- [ ] R1 Release management, the research list the owner asked for: each item decided from
  today's sources in the Decision Log ("Release management"), then built in U7.
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
- [ ] R2 Probes, outside the repository (scratch directory, throwaway projects and clusters),
  written into `gen9-agent/explore/deploy/NOTES.md`:
  - [ ] R2a `docker compose publish` on one Gen9 stack: what it refuses (bind mounts: Gen9
    mounts 16 files and folders, and the Docker socket; `configs:`), `--resolve-image-digests`,
    running it back with `docker compose -f oci://…`, what a multi-project deployment (8 Compose
    projects joined by networks) needs.
  - [ ] R2b `docker compose bridge convert` on one Gen9 stack: what it makes (Deployments or
    StatefulSets, Secrets, networks to NetworkPolicies, volumes), with Docker's default
    transformation and with templates of our own; whether Compose can stay the one definition.
  - [ ] R2c kind and k3d on this machine: create, load or pull images, a Gateway API controller,
    delete; time, memory and disk.
  - [ ] R2d OpenSandbox's Kubernetes runtime on kind: its CRDs and controller chart, the server
    with `[kubernetes]`, a sandbox pod with Gen9's egress image, gVisor or not.
  - [ ] R2e Drift on Kubernetes: `helm diff upgrade` (and its three-way mode) and
    `kubectl diff --server-side`, each against an edit made by hand.
- [ ] U1 Images built once: a bake file for the 7 Gen9 images from the Dockerfiles Compose builds;
  a workflow that builds them for linux/amd64 and linux/arm64, pushes them to
  `ghcr.io/coast-guide/gen9-*`, and attests provenance and SBOM; a lock of their digests in git.
  `make up` keeps building locally for development.
- [ ] U2 Docker from published images: one command deploys a version (or the lock) on any host
  with Docker, pulling by digest, building nothing; the settings in one place.
- [ ] U3 Kubernetes: a Helm 4 chart (or what R2b chooses), standard APIs only, the same images by
  digest, published to GHCR as an OCI chart; verified on kind and on k3d (k3s).
- [ ] U4 Drift: one command per shape (`make drift`) that passes on a fresh deployment and names
  the service after a change by hand.
- [ ] U5 The configuration surface: every setting listed once (domain and TLS, storage class and
  sizes, replicas and resources, secrets source, bundled or external Postgres, Valkey,
  ClickHouse and S3, sandbox runtime), with a schema that rejects unknown keys.
- [ ] U6 Sandboxes on Kubernetes: OpenSandbox's Kubernetes runtime, Gen9's egress and execd
  images, its limits and closed network as on Docker; a chat's command runs in a pod.
- [ ] U7 Releases: the workflow from R1, verified with a pre-release from a branch.
- [ ] U8 The owner's: the first release tag, GHCR packages public.
- [ ] Z1 Docs in step (operations.md, development.md, README, each stack's README, e2e/README,
  gen9-learn); `make e2e` against Docker and kind from published images.

## Surprises & Discoveries

- This machine has Docker 29.8.1 and Compose v5.5.1, and no Kubernetes tooling (no kind, k3d,
  helm, kubectl): `which kind k3d helm kubectl` printed nothing. R2c installs them from their
  release pages, checksums verified, into `~/.local/bin`.
- `docker compose publish` refuses a project "with service(s) containing bind mounts", "containing
  only a `build` section", or including local files with `include`
  ([Docker docs](https://docs.docker.com/compose/how-tos/oci-artifact/)). Gen9's Compose files
  bind-mount configuration 16 times in six stacks (`grep -nE '^\s+- \.{1,2}/' gen9-*/compose.yaml
  gen9-langfuse/*.y*ml`): Keycloak's realm and config, Postgres's initdb and scripts, ClickHouse's
  disk settings, OpenSandbox's `config.toml`, Temporal's initdb, scripts and dynamic config, the
  router's config, SearXNG's settings, the models' scripts and admin page; and OpenSandbox's
  server mounts the Docker socket.
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
  configuration files, rather than upstream charts that swap parts. Rationale: the owner's core
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
- Decision (open, R1): release notes. Gen9's commit subjects are prose (AGENTS.md, "Rules"), and
  release-please "assumes you are using Conventional Commit messages"
  ([release-please](https://github.com/googleapis/release-please)); GitHub's generated notes are
  built from merged pull requests and grouped by labels in `.github/release.yml`
  ([GitHub, generated release notes](https://docs.github.com/en/repositories/releasing-projects-on-github/automatically-generated-release-notes)),
  which fits prose subjects. SemVer 2.0.0 is current; "Major version zero (0.y.z) is for initial
  development. Anything MAY change at any time" ([semver.org](https://semver.org/)).

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
`make deploy-k8s` and `make drift`; the chart under `deploy/`; one settings reference.
