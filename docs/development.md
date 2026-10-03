# Developing Gen9

How the repository is organised for the people and AI agents who change it, how changes are checked,
and how to add a stack. The rules themselves (how work is planned, verified and committed, and
where each kind of knowledge lives) are in [AGENTS.md](../AGENTS.md); how to propose a change is in
[CONTRIBUTING.md](../CONTRIBUTING.md).

## Working on Gen9

Work that spans sessions has a plan in `docs/plans/` ([how plans work](PLANS.md)); which one is
active, and how every session starts, is in [AGENTS.md, "Start of every session"](../AGENTS.md#start-of-every-session).

## Checks

Which checks each part of the repository needs before a commit: [AGENTS.md, "Checks"](../AGENTS.md#checks).

Design system: `make design-sync` copies `gen9-design` into the apps; `make design-check` fails if a copy drifted.

Gen9's own images are built once for every deployment (docs/plans/deploy.md, U1): `docker-bake.hcl` names them, and `.github/workflows/images.yml` builds them with Docker's reusable bake workflow for linux/amd64 and linux/arm64, each on a native runner. On a pull request that changes a stack that builds one, it only builds; on `main` it pushes them to `ghcr.io/coast-guide/<image>` (tags `main` and `sha-<commit>`) with BuildKit's signed provenance and an SBOM, attests each with GitHub (`gh attestation verify oci://ghcr.io/coast-guide/<image>@<digest> -R coast-guide/gen9`), and keeps the digests as the run's `images.lock` artifact. `make up` still builds them on the machine, from the same contexts; `make config` fails if `docker-bake.hcl` and the Compose files differ (`scripts/check-images.py`). `docker buildx bake --set '*.platform=linux/amd64' --load` builds them here as CI does.

Each stack's Helm chart (`gen9-<stack>/chart`, [docs/operations.md, "Kubernetes"](operations.md#kubernetes)) reads the stack's `compose.yaml` through the library chart `deploy/helm/gen9-lib`, so a change to a Compose file changes the chart too. CI's `charts` job renders every chart with a stand-in lock and checks it (`scripts/check-charts.sh`: `helm lint`, then every object against Kubernetes' schemas with `kubeconform -strict`); the template itself fails on a Compose service with no entry in the chart's `services`, a mounted file not linked into the chart, or an image not by digest. `make k8s-e2e` runs `make e2e` against a cluster (`e2e/k8s/docker` stands in for `docker`).

CI (`.github/workflows/checks.yml`) runs on every pull request and on `main`. It checks: types, lint, unit tests, dependency advisories, the design-token copies, gen9-agent's plugin loader against the Agent Plugins conformance kit and, on Linux with real Docker, the make workflow itself (shellcheck, `setup` without a terminal, `doctor`, `config`, then gen9-postgres, gen9-keycloak and gen9-agent up and calling each other, `verify.sh`, gen9-models serving its aliases to gen9-agent's key and to no other stack's network, gen9-temporal up with its namespace, its frontend refusing callers without a token and its internal ports callers without its certificate, and starting over). `make e2e` needs Chrome and every stack, and runs locally.

The repository itself (read on 2026-10-01 with `gh api repos/coast-guide/gen9/rulesets` and the repository's settings; docs/plans/manual-e2e.md, P6-D5):

- `main` takes changes only through a pull request, squash-merged, with its conversations resolved, CI's seven checks passing and no CodeQL alert at error or high; no force push, no deletion, linear history. Release tags (`v*`) can't be moved or deleted. Nobody bypasses either ruleset.
- Workflows run with read-only contents unless a job asks for more, can't approve pull requests, and may use an action only pinned to a full commit SHA. Dependabot proposes the actions' updates weekly, each 7 days old at least.
- Secret scanning with push protection, Dependabot alerts and security updates, private vulnerability reporting ([SECURITY.md](../SECURITY.md)), immutable releases, and CodeQL's default setup (Python, JavaScript and TypeScript, the workflows) are on.
- What that leaves: one maintainer, so a pull request needs no second person's approval (OWASP A03's separation of duties needs a second maintainer, not a setting). Free settings the owner could add: only the three actions the workflow uses allowed (GitHub's `actions/*` and `astral-sh/setup-uv`, instead of all); signed commits required on `main` (GitHub signs the squash merges it makes); CodeQL's extended query suite; secret scanning's non-provider patterns and validity checks, which are off (GitHub's docs don't say whether they're free on a public repository).

Dependencies: `make audit` fails on high or critical advisories in the npm projects and the locked Python packages (gen9-agent, gen9-cli). A new dependency version waits 7 days before it can come in, so a malicious release has had time to be found and pulled: each npm project's `.npmrc` (`min-release-age=7`, npm 11.10 and later; `npm ci` still installs the lockfile as it is) and each Python project's `pyproject.toml` (`exclude-newer = "7 days"`, whose cutoff uv writes into `uv.lock`). A version taken sooner on purpose, after reading its notes and running the checks, is named with its reason (`exclude-newer-package`, `min-release-age-exclude`). Dependencies' install scripts run only when that project's `package.json` allows them (`allowScripts`, managed with `npm install-scripts ls|approve|deny`), and one nobody has reviewed fails the install (`strict-allow-scripts=true` in the `.npmrc`, npm 11.19 and later); today every one is denied, none being needed. Images get no cooldown: Renovate has no release times for them (`minimumReleaseAge` would hold back all or none), and their pins move by hand, after reading the release and checking its signature where its publisher signs (docs/plans/manual-e2e.md, P6-D2).

Signatures and provenance (docs/plans/manual-e2e.md, P6-D4): `make audit`, where a project's dependencies are installed, and CI check every npm package's registry signature, and its provenance where it has one (`npm audit signatures`). When an image's pin moves, its signature is checked where its publisher signs:

| Image | Check |
| --- | --- |
| Chainguard's MinIO (gen9-langfuse) | `cosign verify cgr.dev/chainguard/minio@<digest> --certificate-oidc-issuer https://token.actions.githubusercontent.com --certificate-identity https://github.com/chainguard-images/images/.github/workflows/release.yaml@refs/heads/main` |
| distroless (`scripts/sbom/Dockerfile`) | `cosign verify gcr.io/distroless/static-debian13@<digest> --certificate-oidc-issuer https://accounts.google.com --certificate-identity keyless@distroless.iam.gserviceaccount.com` |
| LiteLLM (gen9-models) | `cosign verify --key https://raw.githubusercontent.com/BerriAI/litellm/0112e53046018d726492c814b3644b7d376029d0/cosign.pub ghcr.io/berriai/litellm@<digest>` (the key at the commit LiteLLM's release notes give) |
| uv (gen9-agent's build) | `gh attestation verify --owner astral-sh oci://ghcr.io/astral-sh/uv@<digest>` |
| OpenSandbox's execd (`gen9-sandbox/execd/Dockerfile`) | `cosign verify opensandbox/execd@<digest> --certificate-oidc-issuer https://token.actions.githubusercontent.com --certificate-identity https://github.com/opensandbox-group/OpenSandbox/.github/workflows/publish-components.yml@refs/tags/docker/execd/<version>` (its egress and server images carry none) |
| Syft and Grype | Their checksum files, signed by Anchore's release workflow: `scripts/sbom/Dockerfile` says how |

The other images publish no signature cosign or GitHub's attestations can check (each looked up on 2026-10-01; Langfuse's, on `docker.langfuse.com`, couldn't be: that registry answers for Docker Hub, whose limit for unauthenticated lookups had been reached). Python: uv doesn't check PyPI's attestations (PEP 740) when it installs, so `make audit` and CI do (`scripts/provenance.py`): for each locked package PyPI holds provenance for (68 of the 160), the Sigstore bundle against its publisher, and its subject against the file's SHA-256 in the lock, with nothing downloaded. Each package's repository is pinned in `scripts/provenance.json`; the check fails when one names another repository, or a package that had provenance has none. After a lock change brings new packages, `uv run scripts/provenance.py --update` pins them: read each first. `uvx pypi-attestations verify pypi --repository https://github.com/<owner>/<repo> <file URL>` checks a single file. Images: `make sbom` and `make scan` (Syft and Grype, pinned and verified; [docs/operations.md, "Images"](operations.md#images-sboms-and-known-vulnerabilities)) read what they hold besides: OS packages, the bases' Python and Node, what the services Gen9 runs bring. A scanner reports a Go binary by its Go version, so check one with `govulncheck -mode binary <file>`, which looks for the vulnerable functions themselves, before accepting it in `scripts/sbom/grype.yaml`. The reviews of every image, finding by finding, are in `docs/plans/manual-e2e.md` (P4-A5, P6-D1). Every image is pinned as `tag@digest`, so a tag rebuilt with its base image's fixes, or a newer release, goes unnoticed: `make updates` lists both, from Renovate's dry run over a copy of the compose files and Dockerfiles (Node.js, about a minute; it changes nothing).

End to end: `make e2e` drives real Chrome against every running stack and checks what a person and an admin can do and what they must not be able to do: signing in and out, chats that keep going without anyone watching, questions and approvals, models through the router within budgets, connectors and their apps, a chat's environment, background and scheduled tasks, Gen9 as an MCP server, an AG-UI and an A2A agent, search, memory, plugins, access control between people and for admins, stopping every agent, data export, the audit record, fairness between people, the database refusing writes or down, passkeys and account recovery, keyboard use, and accessibility on every screen. Each check, step by step, and how to run it in Firefox or WebKit: [e2e/README.md](../e2e/README.md).

Evals: `make evals` measures how dependably Gen9 does real tasks. It runs a suite of tasks through gen9-agent's API as the seeded user, each several times in fresh chats. Code graders, and a model judge where code can't decide, check what each run produced, and every run of a suite is kept in Langfuse as an experiment with its pass rates (`pass@k`, `pass^k`). It spends model calls, so it runs only when you ask (see [gen9-agent/README.md](../gen9-agent/README.md#evals)). `make evals-calibrate` sends a sample of the judge's verdicts to people in Langfuse, and `make evals-calibrate REPORT=1` compares their scores with the judge's.

## How stacks stay decoupled

`make up` and `make config` run `cd gen9-<name> && docker compose …` for each stack, so each keeps its own project name, `.env`, volumes and network, the same as running `docker compose` inside the folder. Both ways manage the same containers. `down`, `ps`, `logs`, `wipe` and `distclean` find a stack's containers, volumes and networks by the project label Compose puts on them (`com.docker.compose.project=gen9-<name>`), so they work even when its setup files are gone; a container counts as the stack's only if it also has Compose's `com.docker.compose.oneoff` label, as Compose itself counts them (the containers of an image Compose built carry its project label too, like OpenSandbox's egress sidecars from gen9-sandbox's image). Stacks share nothing but the `*.local.env` files one stack's setup writes into another's folder, and the per-stack networks below.

Stacks are deliberately not merged with Compose [`include`](https://docs.docker.com/reference/compose-file/include/): that loads everything into one project, which renames every volume (orphaning existing data) and silently keeps only one of two same-named services (for example two `postgres`).

Clients on the host (browser, `gen9-cli`) reach stacks at `localhost:<published port>`. Containers reach another stack over that stack's network, `gen9-<stack>`, at `gen9-<stack>:<container port>`: for example `gen9-postgres:5432`, `gen9-keycloak:8080`, `gen9-temporal:7233`, `gen9-agent:8000`. Each stack joins its own network (with the alias `gen9-<stack>`; gen9-langfuse also puts its media store there, `gen9-langfuse-media`, where gen9-agent's worker uploads a trace's images) and the networks of the stacks it calls, nothing more: gen9-agent joins `gen9-postgres`, `gen9-keycloak`, `gen9-langfuse` and `gen9-temporal`, and `gen9-models` (the worker runs the agent's calls; the API only embeds and reranks search queries, with a key that can do nothing else), gen9-ui joins `gen9-keycloak` and `gen9-agent`, gen9-temporal joins `gen9-keycloak` (Keycloak's keys for tokens, and its web UI's sign-in), gen9-models joins no other stack's network (it reaches model providers over the internet, and only gen9-agent's worker reaches it), and gen9-keycloak joins `gen9-ui` for back-channel logout. So gen9-ui can't reach the database at all. Reaching a stack isn't trusting it: gen9-agent checks tokens, and Temporal's frontend does too, while its internal ports require its own certificate (gen9-temporal/README.md, "Security"). The networks are declared `external`, so no stack owns another's; `make up` creates them (by hand: `docker network create gen9-<stack>`), and `make wipe` removes one once no stack uses it.

This works the same on Linux and Docker Desktop. Going through the host instead (`host.docker.internal` to ports published on `127.0.0.1`) only works on Docker Desktop: on a Linux engine a container can't reach a port published on the host's loopback, and publishing on every interface would expose the databases (Docker's published ports bypass ufw). One network shared by all stacks is out too: Docker registers every service name on every network the service joins, and a container asking for its own `postgres` got the other stack's `postgres` there. `make config` fails if a change would let that happen (`scripts/check-networks.py`).

## Connections, and what each side shows

Every connection Gen9's parts make: whether it's encrypted, and how each side knows the other
(OWASP ASVS 5.0 12.3, 13.2; docs/plans/manual-e2e.md, P7-D1). On one host the stacks talk over
Docker's bridge networks, which stay inside the machine: that is why most aren't encrypted. Each
is still authenticated, and a service sits only on the networks of those that call it (a stack's
database and its other inner parts on its own network alone).

| From | To | Encrypted | The caller shows | The service shows |
| --- | --- | --- | --- | --- |
| A browser, `gen9` on the terminal | gen9-ui, Keycloak, gen9-agent's API, on `127.0.0.1` | Behind the TLS proxy an operator puts in front (each stack's README, "Deploy on a VM"); over https the web app adds HSTS and `Secure` cookies | The session cookie; a sign-in at Keycloak; an access token | The proxy's certificate |
| gen9-ui | gen9-agent's API | No | The person's access token (audience `gen9-agent`) | |
| gen9-ui | Keycloak | No | Its client secret | Signed tokens, their issuer and audience checked |
| gen9-ui | Its Valkey | No | `VALKEY_PASSWORD` | |
| Keycloak | gen9-ui, to sign a session out | No | A signed logout token | |
| gen9-agent's API and worker | gen9-postgres | No | A password (SCRAM-SHA-256), a role for each use | |
| gen9-agent | Keycloak | No | Its client secret (the Admin API) | Signed tokens, their keys from Keycloak's JWKS |
| gen9-agent | The router, the router's admin API | No | A router key; gen9-agent's own key | |
| gen9-agent's worker | Langfuse | No | The project's keys | |
| gen9-agent's worker | OpenSandbox's server, and through it a sandbox's execd | No | `SANDBOX_API_KEY`. execd takes no token: its ports are on `127.0.0.1`, or the bridge's gateway (gen9-sandbox/README.md) | |
| gen9-agent, Temporal's UI | Temporal's frontend | No | A Keycloak token, whose `permissions` say what it may do | |
| Temporal's own services | Each other | Yes: mTLS, with this stack's private CA | Its certificate | Its certificate |
| Temporal, Temporal's UI | Keycloak | No | Temporal's UI: its client secret | Signed tokens |
| Each stack | Its own Postgres, ClickHouse, Redis, MinIO | No | A password | |
| The router | SearXNG, on the stack's own network | No | Nothing | |
| The router | Model providers | Yes: TLS, certificates checked | The provider's key | A public certificate |
| gen9-agent's worker | Connectors, MCP servers, plugin sources | Yes: https only, certificates checked; http only to a private network or a host the operator names | The person's token for that server | A public certificate |
| A sandbox's egress sidecar | The hosts it's allowed | TLS onward, the server's certificate checked | The person's secrets, only to the hosts each is bound to | A public certificate |
| Keycloak, gen9-agent's notices | SMTP | Mailpit: no. A real server: as it's set (gen9-agent: STARTTLS when offered, or `smtps://`) | The server's user, if it has one | |

Checked: every TLS client Gen9 runs (gen9-agent's connector client, the router's Python, whose
LiteLLM keeps `ssl_verify` on, the web app's, Keycloak's) refuses a self-signed certificate and
one for another name; nothing in Gen9 turns the check off, and the egress sidecar only would with `OPENSANDBOX_EGRESS_MITMPROXY_SSL_INSECURE`, which
Gen9 never sets (P7-D2).

**On more than one host**, every connection above that crosses between hosts needs encryption:
TLS on each service, or an encrypted network between the hosts (a VPN, or Docker's encrypted
overlay network). In clear they carry passwords, router and client secrets, people's access tokens
and their chats. Keep gen9-sandbox's host to itself: the Docker socket its server holds is that
host's root, and execd's ports answer anyone who reaches them there. Point Keycloak at a real SMTP
server over TLS (its realm's email settings) and gen9-agent at one with `smtps://` or STARTTLS.

## Add a stack

1. Create `gen9-<name>/` with a Compose file and a README; publish ports on `127.0.0.1` in the next free block (`15000–15099`, …).
2. In the `Makefile`, add `<name>` to `ALL_STACKS` after the stacks it needs, describe it in `DESC_<name>` and list the files it can't start without in `NEEDS_<name>`. If it generates files, teach `scripts/setup.sh` and `scripts/wipe.sh` about them.
3. Check with `make config STACKS=<name>`, then `make up STACKS=<name>`.
