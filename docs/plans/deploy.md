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
  - [ ] `docker-bake.hcl`; `scripts/check-images.py` in `make config` (bake and Compose agree).
  - [ ] `.github/workflows/images.yml`: Docker's bake workflow per image; on `main`, push and
    attest; the lock as the run's artifact.
  - [ ] Verified here: `docker buildx bake --print`; all 7 built for linux/amd64 and two for
    linux/arm64 (emulated); `make config`.
  - [ ] Verified in CI: the pull request builds all 7 for both platforms on native runners.
  - [ ] After the owner merges: the run on `main` pushes, signs and attests;
    `gh attestation verify` on each.
- [ ] U1b Langfuse's S3 store: MinIO's repository is archived ("THIS REPOSITORY IS NO LONGER
  MAINTAINED"); choose a maintained store for both shapes (SeaweedFS, which Langfuse's chart
  bundles, or another), or keep Chainguard's build knowingly; any S3 stays a setting.
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
- [ ] U7 Releases, as R1 decided: labels and `.github/release.yml`; the release workflow (images,
  chart, Compose bundle, SBOMs, attestations, a draft then published); the `release` environment;
  the version in the API, the web app, the CLI and the images' labels; `SECURITY.md`'s supported
  versions; "Releasing" in docs/development.md; Scorecard's workflow. Verified with a pre-release
  from a branch.
- [ ] U8 The owner's: the first release tag; GHCR packages public; the `release` environment's
  reviewer; `v*` tags creatable only by them (the ruleset's creation rule); registering at
  bestpractices.dev if they want the badge.
- [ ] Z1 Docs in step (operations.md, development.md, README, each stack's README, e2e/README,
  gen9-learn); `make e2e` against Docker and kind from published images.

## Surprises & Discoveries

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
