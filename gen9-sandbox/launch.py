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
- **No file names in the access log.** The server logs each request's path with its query, so a
  chat's file names (`files/download?path=/work/out/…`) reached `make logs`. Query values are
  masked there, as gen9-agent's API does (manual-e2e.md, P6-C2; ASVS 5.0 16.2.5).
- **The execd and egress images Compose pulled or built.** OpenSandbox reads them only from its
  config file, which takes no environment override for them; a release names them by digest
  (`make up IMAGES=…`, docs/plans/deploy.md, U2). The server gets a copy of config.toml with
  `[runtime] execd_image` and `[egress] image` set from GEN9_SANDBOX_EXECD_IMAGE and
  GEN9_SANDBOX_EGRESS_IMAGE, which compose.yaml sets to the images of its execd-image and
  egress-image.
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


class QueryValues(logging.Filter):
    """Masks the query values of uvicorn's access line (client, method, path, HTTP version,
    status); the path, its ids and the names of the parameters stay."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) == 5 and isinstance(args[2], str):
            path, _, query = args[2].partition("?")
            if query:
                masked = "&".join(
                    f"{pair.partition('=')[0]}=…" for pair in query.split("&")
                )
                record.args = (*args[:2], f"{path}?{masked}", *args[3:])
        return True


def with_images(text: str, images: dict[tuple[str, str], str]) -> str:
    """config.toml's text with each (section, key) in images set to its value, other lines as they
    are."""
    section, lines = "", []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped.strip("[]").strip()
        key = stripped.split("=", 1)[0].strip()
        if "=" in stripped and (section, key) in images:
            line = f'{key} = "{images[(section, key)]}"'
        lines.append(line)
    return "\n".join(lines) + "\n"


images = {
    (section, key): os.environ[name]
    for section, key, name in [
        ("runtime", "execd_image", "GEN9_SANDBOX_EXECD_IMAGE"),
        ("egress", "image", "GEN9_SANDBOX_EGRESS_IMAGE"),
    ]
    if os.environ.get(name)
}
# The config file the server reads: --config (the image's CMD), which wins over SANDBOX_CONFIG_PATH
args = sys.argv[1:]
at = args.index("--config") + 1 if "--config" in args else -1
config_path = args[at] if at > 0 else os.environ["SANDBOX_CONFIG_PATH"]
if images:
    with open(config_path) as source:
        config = with_images(source.read(), images)
    with open("/tmp/gen9-config.toml", "w") as copy:
        copy.write(config)
    os.environ["SANDBOX_CONFIG_PATH"] = "/tmp/gen9-config.toml"
    if at > 0:
        args[at] = "/tmp/gen9-config.toml"
    # Logging isn't set up yet: the server's own settings come with it
    print(
        f"gen9: execd and egress images: {', '.join(images.values())}", file=sys.stderr
    )

containers.convert_port_bindings = one_address
containers.HostConfig.__init__ = bounded
threading.Thread(target=watch_disk, name="gen9-disk-watch", daemon=True).start()
# On the logger itself: uvicorn's logging settings, applied at start, add handlers and keep it
logging.getLogger("uvicorn.access").addFilter(QueryValues())

# Imported only now, after the patches above
from opensandbox_server.cli import main

sys.argv = ["opensandbox-server", *args]
sys.exit(main())
