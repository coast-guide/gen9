"""Probe: OpenSandbox as the Deep Agents execution environment.

Needs an OpenSandbox server (see NOTES.md) and OPEN_SANDBOX_DOMAIN / OPEN_SANDBOX_API_KEY in the env.

    uv run --no-project --env-file .env --env-file <probe.env> --with opensandbox==1.1.0 \
      --with langchain-sandbox-opensandbox==0.1.2 --with deepagents==0.7.18 \
      --with 'langchain-openai>=1.6.4' python explore/harness/sandbox_probe.py

Prints what it observed, never a secret: the credential it injects is a throwaway probe value.
"""

import asyncio
import os
import time
from datetime import timedelta

from opensandbox import SandboxSync
from opensandbox.config import ConnectionConfig, ConnectionConfigSync
from opensandbox.models.sandboxes import (
    Credential,
    CredentialBinding,
    CredentialProxyConfig,
    NetworkPolicy,
    NetworkRule,
)
from opensandbox.sandbox import Sandbox

IMAGE = "python:3.12-slim"
DOMAIN, KEY = os.environ["OPEN_SANDBOX_DOMAIN"], os.environ["OPEN_SANDBOX_API_KEY"]
FETCH = (
    'python -c "import urllib.request as u,sys\n'
    "try:\n r=u.urlopen(sys.argv[1],timeout=8); print(r.status, r.read(400).decode(errors='replace'))\n"
    "except Exception as e: print('blocked:', type(e).__name__, str(e)[:120])\" "
)


def out(execution) -> str:
    text = "\n".join(m.text for m in execution.logs.stdout)
    err = "\n".join(m.text for m in execution.logs.stderr)
    return (text + (f" | stderr: {err[:200]}" if err.strip() else "")).strip()


async def sdk_checks() -> None:
    config = ConnectionConfig(
        domain=DOMAIN, api_key=KEY, protocol="http", use_server_proxy=True
    )
    t = time.monotonic()
    sandbox = await Sandbox.create(
        IMAGE,
        connection_config=config,
        timeout=timedelta(minutes=10),
        network_policy=NetworkPolicy(
            defaultAction="deny",
            egress=[NetworkRule(action="allow", target="httpbin.org")],
        ),
        credential_proxy=CredentialProxyConfig(enabled=True),
    )
    print(f"created {sandbox.id} in {time.monotonic() - t:.1f}s")
    try:
        print("python:", out(await sandbox.commands.run("python -c 'print(2**10)'")))
        print("whoami:", out(await sandbox.commands.run("id -u; id -un")))
        print(
            "allowed host:",
            out(await sandbox.commands.run(FETCH + "https://httpbin.org/get?probe=1"))[
                :120
            ],
        )
        print(
            "denied host:",
            out(await sandbox.commands.run(FETCH + "https://example.com/")),
        )
        print(
            "cloud metadata:",
            out(
                await sandbox.commands.run(
                    FETCH + "http://169.254.169.254/latest/meta-data/"
                )
            ),
        )
        await sandbox.credential_vault.create(
            credentials=[
                Credential(name="probe-token", source={"value": "probe-secret-4f1c"})
            ],
            bindings=[
                CredentialBinding(
                    name="echo",
                    match={
                        "schemes": ["https"],
                        "hosts": ["httpbin.org"],
                        "paths": ["/headers"],
                    },
                    auth={
                        "type": "apiKey",
                        "name": "x-probe-key",
                        "credential": "probe-token",
                    },
                )
            ],
        )
        env = out(await sandbox.commands.run("env | grep -ci probe-secret || true"))
        print("secret visible in sandbox env:", env)
        echoed = out(await sandbox.commands.run(FETCH + "https://httpbin.org/headers"))
        print("upstream received x-probe-key:", "x-probe-key" in echoed.lower())
    finally:
        await sandbox.destroy()
        print("destroyed")


def agent_check() -> None:
    from deepagents import create_deep_agent
    from langchain_opensandbox import OpenSandboxBackend

    config = ConnectionConfigSync(
        domain=DOMAIN, api_key=KEY, protocol="http", use_server_proxy=True
    )
    sandbox = SandboxSync.create(
        IMAGE, connection_config=config, timeout=timedelta(minutes=10)
    )
    try:
        agent = create_deep_agent(
            model="openai:gpt-5.5",
            backend=OpenSandboxBackend(sandbox=sandbox),
            system_prompt="You work in a Linux sandbox. Be brief.",
        )
        result = agent.invoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": "Write /workspace/fib.py printing the 30th Fibonacci number, run it, and tell me the number.",
                    }
                ]
            }
        )
        calls = [
            c["name"] for m in result["messages"] for c in getattr(m, "tool_calls", [])
        ]
        print("agent tool calls:", calls)
        print("agent answer:", result["messages"][-1].text[:200])
        print(
            "file in sandbox:",
            sandbox.commands.run("cat /workspace/fib.py | head -3").logs.stdout[0].text,
        )
    finally:
        sandbox.kill()
        sandbox.close()


asyncio.run(sdk_checks())
if not os.environ.get("SKIP_AGENT"):
    agent_check()
