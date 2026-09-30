"""Is a Docker-runtime sandbox's execd reachable without a token, and on which interfaces? (NOTES.md)

Needs this folder's server (compose.yaml). It calls execd at 127.0.0.1 and at this machine's address
on the network (en0).

    uv run --no-project --with opensandbox==1.1.0 --with httpx python explore/sandbox/probe.py
"""

import asyncio
import json
import subprocess
from datetime import timedelta

import httpx
from opensandbox.config import ConnectionConfig
from opensandbox.models.sandboxes import NetworkPolicy
from opensandbox.sandbox import Sandbox

FETCH = (
    'python -c "import urllib.request as u,sys\n'
    "try:\n print(u.urlopen(sys.argv[1],timeout=8).status)\n"
    "except Exception as e: print('blocked:', type(e).__name__)\" "
)


async def run(*command: str) -> str:
    # The docker and ipconfig CLIs have no async API: in a thread
    result = await asyncio.to_thread(
        subprocess.run, list(command), capture_output=True, text=True, check=False
    )
    return result.stdout


async def main() -> None:
    config = ConnectionConfig(
        domain="127.0.0.1:20999",
        api_key="probe-key-not-secret",
        protocol="http",
        use_server_proxy=True,
    )
    sandbox = await Sandbox.create(
        "python:3.12-slim",
        connection_config=config,
        timeout=timedelta(minutes=5),
        network_policy=NetworkPolicy(defaultAction="deny", egress=[]),
    )
    try:
        ran = await sandbox.commands.run("id -u")
        print("through the server:", ran.logs.stdout[0].text)
        blocked = await sandbox.commands.run(FETCH + "https://example.com/")
        print("egress to an undeclared host:", blocked.logs.stdout[0].text)
        names = [
            n
            for n in (await run("docker", "ps", "--format", "{{.Names}}")).split()
            if sandbox.id in n
        ]
        lan = (await run("ipconfig", "getifaddr", "en0")).strip() or "127.0.0.2"
        for name in names:
            ports = (
                json.loads(
                    await run(
                        "docker",
                        "inspect",
                        name,
                        "--format",
                        "{{json .NetworkSettings.Ports}}",
                    )
                )
                or {}
            )
            print(name, {k: v for k, v in ports.items() if v})
            for key, binds in ports.items():
                if not (key.startswith("44772") and binds):
                    continue
                port = binds[0]["HostPort"]
                for host in ("127.0.0.1", lan):
                    try:
                        async with httpx.AsyncClient(timeout=5) as http:
                            r = await http.post(
                                f"http://{host}:{port}/command",
                                json={"command": "echo reached-execd-without-a-token"},
                            )
                        print(
                            f"execd at {host}:{port} with no token: HTTP {r.status_code}"
                        )
                    except httpx.HTTPError as e:
                        print(f"execd at {host}:{port}: {type(e).__name__}")
    finally:
        await sandbox.kill()
        print("killed")


asyncio.run(main())
