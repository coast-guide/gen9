# gen9-sandbox

[OpenSandbox](https://github.com/opensandbox-group/OpenSandbox)'s server (Apache-2.0, `release-1.1.0`),
which runs each chat's environment: a container of its own where the agent runs commands and keeps
files (gen9-agent's `environments.py`). gen9-agent's worker is its only client; nothing else reaches it.

It starts sandboxes through the Docker socket, which is the host's root. That's fine on one machine you
own. A deployment runs OpenSandbox's Kubernetes runtime, or this stack on a host of its own, and gVisor
or Kata where it can (`[secure_runtime]` in `config.toml`).

## Quick start

```bash
./init-env.sh --agent-env-file ../gen9-agent/sandbox.local.env   # or: make setup STACKS=sandbox
docker compose up -d --build --wait                            # or: make up STACKS=sandbox
```

## How a chat's environment works

| Piece | Where | Notes |
| --- | --- | --- |
| The server | `opensandbox` service | OpenSandbox's image, pinned by digest, started by `launch.py`. Its API (`OPEN-SANDBOX-API-KEY` from `.env`) on `gen9-sandbox:8090` for gen9-agent's worker, and on `127.0.0.1:20000` |
| A sandbox | containers `sandbox-<id>` and `sandbox-egress-<id>` | Started by the server as siblings on Docker's default bridge: the chat's image (`SANDBOX_IMAGE`, `python:3.12-slim`), OpenSandbox's execd inside, and its egress sidecar. Labelled with the chat and the person (`gen9-thread`, `gen9-user`) |
| Its network | the egress sidecar, `dns+nft` | Closed but for the hosts gen9-agent allows (`SANDBOX_EGRESS_ALLOW`, none by default) and the hosts of the person's secrets. Never private, shared (CGNAT), link-local or cloud-metadata addresses, whatever is allowed, nor their IPv6 forms (IPv4-mapped, and NAT64's `64:ff9b::/96` and `64:ff9b:1::/48`, which a NAT64 gateway on an IPv6-enabled network turns back into IPv4): `egress/deny.always`, OpenSandbox's platform baseline, which a sandbox's policy can't lift. An internal host a deployment needs goes in an `allow.always` next to it, knowingly. Each change to its rules is also written to `/var/egress/policy.json` in the sidecar (`OPENSANDBOX_EGRESS_POLICY_FILE`, set in `egress/Dockerfile`), which egress reads first when it restarts after a crash; without it, a restarted egress went back to the rules the sandbox was created with and reopened hosts closed since A lookup the policy denies is logged by the sidecar, with the host (`[dns] denied by policy`). |
| The person's secrets | the egress sidecar's credential vault | Added to https requests to their host on the way out, only with the methods the person chose (GET, HEAD and OPTIONS unless they chose changes too: the binding's `match.methods`); the sandbox's processes never hold them, though a host that echoes requests back shows its own secret. Only onto a TLS session whose certificate proves a host the secret is for: the vault chose by the Host header alone, which the sandbox writes, so code could send one secret to another allowed host (OpenSandbox #1758; `egress/sni-binding.py` carries its fix, PR #1759). Written by gen9-agent's worker when the sandbox is created, and rewritten while it runs when the person changes them. The vault lives in the sidecar's memory: a new sidecar starts empty, and so does egress restarted after a crash (OpenSandbox #1366, #1594); gen9-agent writes it again at the chat's next command |
| Its life | gen9-agent's `EnvironmentWorkflow` | Created on the chat's first command or file, renewed while used, removed after `SANDBOX_IDLE_S` (30 min) unused or when the chat or the account is deleted. OpenSandbox's own timeout (twice the idle time, at most a day: `max_sandbox_timeout_seconds`) removes it even if Temporal can't. The server keeps each renewal in the `metadata` volume (`/root/.opensandbox`), so a recreated server (an upgrade, `make down` then `make up`) still knows them; it restores its timers from the running containers when it starts |
| Its limits | `config.toml`, `launch.py`, gen9-agent's settings | 1 CPU and 1 GiB each (`SANDBOX_CPU`, `SANDBOX_MEMORY`), without swap; Docker's seccomp filter, capabilities dropped (raw sockets among them), `no_new_privileges`, 4096 processes; no Docker socket. Its log: Docker's `local` driver, 10 MB × 3 (execd 1.1.0 logs the start of every output chunk; fixed upstream after it, 9c35ca436). Its disk: `SANDBOX_DISK_GB` (10), checked every `SANDBOX_DISK_CHECK_S` (10) by `launch.py`, which deletes a sandbox past it, container, sidecar and volume; Docker limits a container's disk only on XFS with project quotas, and OpenSandbox's Docker runtime sets none (manual-e2e.md, P5-C5) |

## The sandboxes' ports stay off the network

OpenSandbox 1.1.0 publishes each sandbox's execd on every interface, and execd runs commands without a
token: anyone on your network could run commands in any chat's environment. `launch.py` starts the
server with every port binding moved to `SANDBOX_PUBLISH_HOST`, at docker-py's conversion
(gen9-agent/explore/sandbox/NOTES.md):

- `127.0.0.1` (the default) on Docker Desktop, whose `host.docker.internal` reaches the host's loopback;
- on Linux, the Docker bridge's gateway address (`docker network inspect bridge`), which containers
  reach through `host-gateway` and the network doesn't.

gen9-agent reaches sandboxes only through the server (`use_server_proxy`). A process on the host itself
can still reach a sandbox's execd: the same trust as the Docker socket the server holds.

## Files

| File | Purpose |
| --- | --- |
| `compose.yaml` | `opensandbox` (built here) |
| `Dockerfile` | OpenSandbox's server image, pinned by digest, with `launch.py` |
| `launch.py` | Starts the server with sandboxes' ports on `SANDBOX_PUBLISH_HOST`; bounds each sandbox (disk, logs, no swap) and removes one over its disk (`SANDBOX_DISK_GB`, checked every `SANDBOX_DISK_CHECK_S`); masks the query values in its access log, so a chat's file names stay out |
| `ruff.toml` | Lint rules for this stack's Python (as gen9-agent's: async-first); checked from gen9-agent, as CI does |
| `config.toml` | The server's settings (runtime, the execd and egress images Compose builds, limits, store); no secrets. Read at start: after changing it, `docker compose restart opensandbox` |
| `egress/` | The egress sidecar every sandbox gets: OpenSandbox's, rebuilt by Gen9 from the same release with `deny.always`. OpenSandbox builds it on Debian 12 with Go 1.25.9 and mitmproxy 11.0.2, which caps `h11` (Critical), `cryptography`, `tornado` and `pyOpenSSL` below their fixes, and mitmproxy 12 needs Python 3.12. So `egress/Dockerfile` builds its two Go binaries from the release's commit with a supported Go (govulncheck: 14 reached to none), and puts them on Debian 13 with upstream's packages, mitmproxy 12.2.3 from `requirements.txt` (a lock with hashes, from `requirements.in`, with `overrides.txt` lifting mitmproxy's caps on `h2`, `tornado`, `cryptography`, `msgpack` and `pyOpenSSL` to their fixed versions), Debian's updates, and upstream's addon, mitmproxy settings and cleanup script copied from OpenSandbox's image of that release, pinned by digest (docs/plans/manual-e2e.md, P6-D1c5). Compose builds it (`egress-image`, which runs `true` and exits) before the server starts. The sidecars inherit the image's Compose labels (`com.docker.compose.project=gen9-sandbox`), not `com.docker.compose.oneoff`: `make ps`, `down` and `wipe` leave them out of the stack's own containers, and `wipe` removes them with the environments |
| `execd/` | The execd the server copies into every sandbox to run its commands: OpenSandbox's image pinned by digest, with `/execd` rebuilt from that release's commit with a current Go and the modules OpenSandbox's main has moved to (OpenSandbox builds it with Go 1.25.9 and grpc 1.82.1, in which govulncheck finds 21 reached vulnerabilities; none rebuilt), the binaries the server never takes from it removed (the eBPF and Windows builds, the supervisor), and Alpine's updates. Compose builds it (`execd-image`, which runs `true` and exits), and `config.toml`'s `execd_image` names it (docs/plans/manual-e2e.md, P6-D1c5c) |
| `init-env.sh` | Generates `.env` (the API key) and gen9-agent's `sandbox.local.env`. See `--help` |
| `.env` | Generated, secret, gitignored |

## Ports

| Port | Service | `.env` variable |
| --- | --- | --- |
| `20000` | The server's API | `GEN9_SANDBOX_PORT` |
| `40000-40999` | Sandboxes' own (3 each), on `SANDBOX_PUBLISH_HOST` | `config.toml` |

## Verify

```bash
curl -fsS http://127.0.0.1:20000/health                                            # {"status":"healthy"}
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:20000/v1/sandboxes       # 401: it wants its key
cd ../e2e && npm run environments                                                   # a chat's environment, end to end
```

## Operate

| Task | Command |
| --- | --- |
| Status | `docker compose ps` |
| Logs | `docker compose logs --tail=50 opensandbox` |
| The environments running | `docker ps --filter label=opensandbox.io/id` (a chat's: `--filter label=gen9-thread=<chat id>`) |
| Remove every environment | `make wipe STACKS=sandbox` (their containers, and the server's records) |
| Upgrade OpenSandbox | The digests in `Dockerfile`, `egress/Dockerfile` and `execd/Dockerfile`. In `egress/Dockerfile` and `execd/Dockerfile`, also each `COMMIT`: the commit of the new release's tag (egress's `release-…`, execd's `docker/execd/…`; `gh api repos/opensandbox-group/OpenSandbox/git/ref/tags/<tag>`, then the tag object's commit), and the Go image, the newest patch of the Go that release builds with; in `execd/Dockerfile`, the module versions too; for egress, the mitmproxy lock again (`uv pip compile requirements.in --universal --python-version 3.13 --generate-hashes -o requirements.txt`) after reading mitmproxy's changelog against the addon. `egress/Dockerfile` fails until `sni-binding.py` is dropped, once the egress release has PR #1759, or its checksum of `system.py` is updated after checking the file. Then `make sbom STACKS=sandbox && make scan`, and rerun `gen9-agent/explore/sandbox/probe.py` to check `launch.py` still keeps the ports off the network |
