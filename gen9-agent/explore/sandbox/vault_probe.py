"""Credentials at egress on a running sandbox (gen9-sandbox, OpenSandbox 1.1.0): a host and a secret
added after creation, then removed. httpbin.org echoes the request's headers. The value is a
throwaway made here.

    SANDBOX_API_KEY=… uv run python explore/sandbox/vault_probe.py
"""

import asyncio
import os
import secrets
from datetime import timedelta

from opensandbox.config import ConnectionConfig
from opensandbox.models.sandboxes import (
    Credential,
    CredentialBinding,
    CredentialProxyConfig,
    NetworkPolicy,
    NetworkRule,
)
from opensandbox.sandbox import Sandbox

FETCH = (
    'python3 -c "import urllib.request as u, json\n'
    "try:\n r = u.urlopen('https://httpbin.org/headers', timeout=10)\n"
    " print('auth:', json.loads(r.read())['headers'].get('Authorization', 'none'))\n"
    "except Exception as e: print('blocked:', type(e).__name__)\""
)


async def main() -> None:
    config = ConnectionConfig(
        domain="127.0.0.1:20000",
        api_key=os.environ["SANDBOX_API_KEY"],
        protocol="http",
        use_server_proxy=True,
    )
    value = f"probe-{secrets.token_hex(8)}"
    sandbox = await Sandbox.create(
        "python:3.12-slim",
        connection_config=config,
        timeout=timedelta(minutes=5),
        network_policy=NetworkPolicy(defaultAction="deny", egress=[]),
        credential_proxy=CredentialProxyConfig(enabled=True),
    )

    async def fetch() -> str:
        out = await sandbox.commands.run(FETCH)
        text = "\n".join(m.text for m in out.logs.stdout)
        return text.replace(value, "<the value>")

    try:
        print("created closed:", await fetch())
        await sandbox.patch_egress_rules(
            [NetworkRule(action="allow", target="httpbin.org")]
        )
        print("host allowed, no secret:", await fetch())
        await sandbox.credential_vault.create(
            credentials=[Credential(name="echo", source={"value": value})],
            bindings=[
                CredentialBinding(
                    name="echo",
                    match={
                        "schemes": ["https"],
                        "hosts": ["httpbin.org"],
                        "paths": ["/*"],
                    },
                    auth={"type": "bearer", "credential": "echo"},
                )
            ],
        )
        print("secret added:", await fetch())
        env = await sandbox.commands.run("cat /proc/1/environ | tr '\\0' '\\n'; env")
        seen = any(value in m.text for m in env.logs.stdout)
        print("the value in the environment's own processes:", seen)
        await sandbox.credential_vault.delete()
        print("secret removed:", await fetch())
        await sandbox.delete_egress_rules(["httpbin.org"])
        print("host closed again:", await fetch())
    finally:
        await sandbox.kill()


asyncio.run(main())
