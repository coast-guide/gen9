"""Starts OpenSandbox's server with Gen9's limits on the containers it makes (docs/plans/
manual-e2e.md, P4-B and P5-C5).

- **Ports on one address, never all interfaces.** OpenSandbox 1.1.0's Docker runtime publishes
  each sandbox's execd, which runs commands without a token, on 0.0.0.0: anyone on the network
  could run commands in any chat's environment (probed, gen9-agent/explore/sandbox/NOTES.md). Every
  container the server creates gets its port bindings through docker-py's HostConfig, which
  converts them here; a binding on all interfaces is moved to SANDBOX_PUBLISH_HOST: 127.0.0.1 on
  Docker Desktop, whose host.docker.internal reaches the host's loopback, or the Docker bridge's
  gateway address on Linux.
- **Bounded logs, no swap.** The server sets neither: a sandbox's log was Docker's json-file,
  unbounded, and execd 1.1.0 writes the start of every output chunk there (fixed upstream after
  1.1.0: 9c35ca436). Its memory limit also came with as much swap again. Each container it makes
  gets the stacks' log settings (the local driver, 10 MB × 3), and its memory limit without swap.
- **A disk limit.** Docker limits a container's disk only on XFS with project quotas, which
  Docker Desktop's containerd store isn't, and OpenSandbox's Docker runtime passes none: a
  command could fill the disk every stack shares (probed: 5 GiB taken at once). A thread here
  checks each sandbox's writable layer every SANDBOX_DISK_CHECK_S and deletes one past
  SANDBOX_DISK_GB through the server's own API, which removes its container, egress sidecar and
  volume, so the disk comes back at once (a kill, if that fails). gen9-agent finds it gone and
  gives the chat a fresh environment on its next command. Nothing inside a sandbox can reach it.
"""

import logging
import os
import sys
import threading
import time
import urllib.error
import urllib.request

import docker
from docker.types import LogConfig, containers

PUBLISH_HOST = os.environ.get("SANDBOX_PUBLISH_HOST") or "127.0.0.1"
LOGS = LogConfig(type="local", config={"max-size": "10m", "max-file": "3"})
DISK_GB = float(os.environ.get("SANDBOX_DISK_GB") or 10)
DISK_CHECK_S = float(os.environ.get("SANDBOX_DISK_CHECK_S") or 10)
# Sandboxes and their egress sidecars, as OpenSandbox labels them
SANDBOX, SIDECAR = "opensandbox.io/id", "opensandbox.io/egress-sidecar-for"
# The server this process runs, as its clients reach it
SERVER = "http://127.0.0.1:8090"

# A child of the server's own logger, which its logging config sends to its output
log = logging.getLogger("opensandbox_server.gen9_launch")
_convert = containers.convert_port_bindings
_host_config = containers.HostConfig.__init__


def one_address(port_bindings):
    converted = _convert(port_bindings)
    for bindings in converted.values():
        for binding in bindings:
            if binding.get("HostIp") in ("", "0.0.0.0", "::"):
                binding["HostIp"] = PUBLISH_HOST
    return converted


def bounded(self, *args, **kwargs):
    """docker-py's HostConfig, with bounded logs and memory without swap unless the server set
    them (it calls it with keywords only)."""
    if kwargs.get("log_config") is None:
        kwargs["log_config"] = LOGS
    if kwargs.get("mem_limit") is not None and kwargs.get("memswap_limit") is None:
        kwargs["memswap_limit"] = kwargs["mem_limit"]
    _host_config(self, *args, **kwargs)


def delete_sandbox(sandbox_id: str) -> None:
    """Deleted as gen9-agent deletes one: container, sidecar and volume."""
    request = urllib.request.Request(
        f"{SERVER}/v1/sandboxes/{sandbox_id}",
        method="DELETE",
        headers={"OPEN-SANDBOX-API-KEY": os.environ["OPENSANDBOX_SERVER_API_KEY"]},
    )
    with urllib.request.urlopen(request, timeout=30):
        pass


def watch_disk() -> None:
    """Ends any sandbox whose writable layer is past DISK_GB. docker-py has no async API, and the
    server's event loop is uvicorn's: a daemon thread of its own, sleeping between checks."""
    api = docker.from_env().api
    limit = DISK_GB * 1024**3
    while True:
        time.sleep(DISK_CHECK_S)
        try:
            for label in (SANDBOX, SIDECAR):
                for c in api.containers(filters={"label": label}, size=True):
                    used = c.get("SizeRw") or 0
                    if used <= limit:
                        continue
                    name = (c.get("Names") or [c["Id"][:12]])[0].lstrip("/")
                    how = "deleted"
                    try:
                        delete_sandbox((c.get("Labels") or {})[label])
                    except (OSError, urllib.error.URLError, KeyError) as e:
                        api.kill(c["Id"])
                        how = f"stopped (deleting it failed: {e})"
                    log.warning(
                        "sandbox container %s used %.1f GiB of disk, over SANDBOX_DISK_GB=%g: %s",
                        name,
                        used / 1024**3,
                        DISK_GB,
                        how,
                    )
        # A check that fails is tried again next time
        except (docker.errors.DockerException, OSError) as e:
            log.warning("disk check failed: %s", e)


containers.convert_port_bindings = one_address
containers.HostConfig.__init__ = bounded
threading.Thread(target=watch_disk, name="gen9-disk-watch", daemon=True).start()

# Imported only now, after the patches above
from opensandbox_server.cli import main

sys.argv = ["opensandbox-server", *sys.argv[1:]]
sys.exit(main())
