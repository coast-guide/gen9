# The sandbox's ports

`probe.py` against this folder's throwaway server (`compose.yaml`; OpenSandbox server
`release-1.1.0`, execd `v1.1.0`, egress `v1.1.7`, Docker Desktop on macOS). It creates one sandbox
with a default-deny network policy, runs a command through the server, then calls the sandbox's
execd (`POST /command`) directly, with no token, at 127.0.0.1 and at this machine's network address.

| Server | Sandbox ports | execd with no token |
| --- | --- | --- |
| as shipped | `0.0.0.0:<port>` for execd (44772), HTTP (8080) and the egress API (18080), on the egress sidecar | ran the command from 127.0.0.1 **and from the network address**: anyone on the network could run commands in any sandbox |
| `port_allocator.DOCKER_PUBLISH_HOST = "127.0.0.1"` | only the egress API moved to loopback: `networking.py` hard-codes `0.0.0.0` for the sidecar's execd and HTTP ports | still ran from the network address |
| `launch.py`: docker-py's `convert_port_bindings` rewrites every binding on all interfaces to 127.0.0.1 | all three on 127.0.0.1 | 127.0.0.1 only; the network address refused the connection. The server still created the sandbox and ran commands through `host.docker.internal` (Docker Desktop reaches the Mac's loopback) |

Also tried, for Linux, where `host.docker.internal` can't reach the host's loopback:
- sandboxes on a user-defined network, the server's proxy going to their addresses
  (`resolve_internal = true`): refused by OpenSandbox, "networkPolicy is not supported when docker
  network_mode='…' (user-defined network). Use network_mode='bridge'";
- the server on Docker's default bridge as well: Compose can't attach a service there ("network-scoped
  aliases are only supported for user-defined networks").

So on Linux the launcher should publish on the Docker bridge's gateway address (reachable from
containers through `host-gateway`, not from the network). Security reports go to OpenSandbox privately
(GitHub private vulnerability reporting, `SECURITY.md`); `secureAccess` tokens exist only for
Kubernetes with the gateway ingress.

# A backend shared by every run

`backend_probe.py` (deepagents 0.7.18, a scripted model, a `BaseSandbox` that records what it sees):
- a `CompositeBackend` whose default is the sandbox, with Gen9's permissions (all under the
  `/memories/` and `/skills/` routes) and `artifacts_root` on a route of its own, builds; the model
  is offered `execute`;
- one backend instance served two threads' runs, and `langgraph.config.get_config()` inside its
  `aexecute` gave each run's own `thread_id` (`thread-a`, then `thread-b`): the adapter can find
  the run's environment per call, as `ConnectorTools` finds the run's person.

# Credentials at egress on a running sandbox

`vault_probe.py` on gen9-sandbox: a sandbox created closed, with the credential proxy on, then
changed while it runs. httpbin.org echoes the request's headers; the value is a throwaway.

| Step | What the sandbox saw |
| --- | --- |
| created, default deny, no rules | `https://httpbin.org` blocked (URLError) |
| `patch_egress_rules([allow httpbin.org])` | reached, no `Authorization` |
| `credential_vault.create(credential, binding: https, httpbin.org, /*, bearer)` | `Authorization: Bearer <the value>` |
| its own processes' environment (`/proc/1/environ`, `env`) | no trace of the value |
| `credential_vault.delete()` | reached, no `Authorization` |
| `delete_egress_rules(["httpbin.org"])` | blocked again |

So a person's secrets can reach their running environments, and leave them, without restarting.

# A sandbox killed under a command

`killed_probe.py` on this folder's server (opensandbox 1.1.0): `echo begin; sleep 30` in a new
sandbox, `docker kill` of its container 3 s in.

| Asked | What came back |
| --- | --- |
| the running command | returned: exit code `None`, stdout `begin` (on gen9-sandbox the same kill made it raise `SandboxApiException`, "Server disconnected without sending a response", 500) |
| `get_info()` then and 5 s later | state `Failed`, reason `CONTAINER_EXITED_ERROR` |
| `is_healthy()` | `False`, after the SDK's own retries of `/ping` (502) |
| another command | `SandboxApiException`, 502 `BACKEND_CONNECTION_FAILED`, and `e.__cause__ is e` |
| `sandbox.kill()` | done, no error |

So a stopped sandbox never comes back, and `get_info` tells it apart from OpenSandbox not
answering. Every SDK call ends `raise ExceptionConverter.to_sandbox_exception(e) from e`, which
returns `e` for the SDK's own errors: each is its own cause (still so on OpenSandbox's main,
`sdks/sandbox/python/.../command_adapter.py`). Temporal's failure converter follows causes
without looking back and fails with RecursionError, losing the message
(temporalio/sdk-python#697, open): Gen9's converter (`codec.py`) stops where the chain loops.

# A command whose call is cancelled

`cancel_probe.py` on this folder's server (opensandbox 1.1.0, execd v1.1.0 at 48b0215): `sleep
60; echo late` from a task, the task cancelled 3 s in, then the container's `/proc` read.

| After the cancel | Cancellation alone | `commands.interrupt(id)` when cancelled |
| --- | --- | --- |
| 1 s | `bash -c sleep 60; echo late` and `sleep 60` running | gone |
| 5 s | both running | gone |
| 20 s | both running | gone |

The SDK closes its stream, but nothing reaches execd through the server's proxy
(`use_server_proxy`), so the command runs on to its limit, although execd kills a command's
process group when its own request ends (`pkg/runtime/command.go`, "client disconnect"). The id
comes in the stream's first event (`init`, `ExecutionHandlers.on_init`); `DELETE /command?id=`
sends the group SIGTERM, then SIGKILL after 3 s (`pkg/runtime/interrupt.go`). Found by hand on
the stacks (manual-e2e.md, P2-I3): an A2A `CancelTask` stopped the run while its `sleep 90` ran
on in the chat's environment.
