"""Does a sandbox on OpenSandbox's Kubernetes runtime run commands with Gen9's egress closed? (NOTES.md)

Needs OpenSandbox's chart on a cluster (NOTES.md, R2d) and its server forwarded to 127.0.0.1:25090:

    kubectl -n opensandbox-system port-forward svc/opensandbox-server 25090:80
    OPENSANDBOX_API_KEY_FILE=… uv run --no-project --with opensandbox==1.1.0 \
        python explore/deploy/sandbox_k8s.py
"""

import asyncio
import os
from datetime import timedelta
from pathlib import Path

from opensandbox.config import ConnectionConfig
from opensandbox.models.sandboxes import NetworkPolicy, NetworkRule
from opensandbox.sandbox import Sandbox

FETCH = (
    'python -c "import urllib.request as u,sys\n'
    "try:\n print(u.urlopen(sys.argv[1],timeout=8).status)\n"
    "except Exception as e: print('blocked:', type(e).__name__)\" "
)


async def main() -> None:
    # One small file read once at start, before any I/O runs concurrently
    key = Path(os.environ["OPENSANDBOX_API_KEY_FILE"]).read_text().strip()  # noqa: ASYNC240
    config = ConnectionConfig(
        domain="127.0.0.1:25090", api_key=key, protocol="http", use_server_proxy=True
    )
    sandbox = await Sandbox.create(
        "python:3.12-slim",
        connection_config=config,
        timeout=timedelta(minutes=10),
        network_policy=NetworkPolicy(
            defaultAction="deny",
            egress=[NetworkRule(action="allow", target="pypi.org")],
        ),
    )
    print("sandbox:", sandbox.id)
    try:
        for label, command in [
            ("user", "id -u"),
            ("allowed host", FETCH + "https://pypi.org/simple/"),
            ("undeclared host", FETCH + "https://example.com/"),
            ("cloud metadata", FETCH + "http://169.254.169.254/"),
        ]:
            ran = await sandbox.commands.run(command)
            out = (
                ran.logs.stdout[0].text.strip() if ran.logs.stdout else ran.logs.stderr
            )
            print(f"{label}: {out}")
    finally:
        if not os.environ.get("KEEP"):
            await sandbox.kill()


asyncio.run(main())
