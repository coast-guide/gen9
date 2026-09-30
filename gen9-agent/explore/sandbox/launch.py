import sys

from docker.types import containers

# Sandboxes' ports on loopback only: OpenSandbox 1.1.0 publishes them on every interface, and
# execd takes commands without a token. Every container the server creates gets its port
# bindings through docker-py's HostConfig, which converts them here
_convert = containers.convert_port_bindings


def loopback_only(port_bindings):
    converted = _convert(port_bindings)
    for bindings in converted.values():
        for binding in bindings:
            if binding.get("HostIp") in ("", "0.0.0.0", "::"):
                binding["HostIp"] = "127.0.0.1"
    return converted


containers.convert_port_bindings = loopback_only

from opensandbox_server.cli import main

sys.argv = ["opensandbox-server", "--config", "/etc/opensandbox/config.toml"]
sys.exit(main())
