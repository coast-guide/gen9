"""What does a sandbox whose container was killed look like to the SDK? (NOTES.md)

Needs this folder's server (compose.yaml). It runs `sleep 30` in a new sandbox, kills the
container during it, and prints what the command, `get_info`, `is_healthy` and a second command
report, and whether each exception is its own cause.

    uv run --no-project --with opensandbox==1.1.0 python killed_probe.py
"""

import asyncio
import subprocess
from datetime import timedelta

from opensandbox.config import ConnectionConfig
from opensandbox.sandbox import Sandbox


async def docker(*args: str) -> str:
    # The docker CLI has no async API: in a thread
    result = await asyncio.to_thread(
        subprocess.run, ["docker", *args], capture_output=True, text=True, check=False
    )
    return (result.stdout + result.stderr).strip()


def described(e: BaseException) -> str:
    return f"{type(e).__name__} (its own cause: {e.__cause__ is e}): {str(e)[:160]}"


async def main() -> None:
    config = ConnectionConfig(
        domain="127.0.0.1:20999",
        api_key="probe-key-not-secret",
        protocol="http",
        use_server_proxy=True,
    )
    sandbox = await Sandbox.create(
        "python:3.12-slim", connection_config=config, timeout=timedelta(minutes=5)
    )
    try:
        info = await sandbox.get_info()
        print("created:", info.status.state)
        names = await docker("ps", "--format", "{{.Names}}")
        container = next(
            n for n in names.splitlines() if sandbox.id in n and "egress" not in n
        )
        print("container:", container)

        async def kill_soon() -> None:
            await asyncio.sleep(3)
            print("kill:", await docker("kill", container))

        killer = asyncio.create_task(kill_soon())
        try:
            ran = await sandbox.commands.run("echo begin; sleep 30; echo end")
            print("command returned:", ran.exit_code, ran.logs.stdout)
        except Exception as e:  # noqa: BLE001 (a probe: whatever it raises is the finding)
            print("command raised:", described(e))
        await killer

        for wait in (0, 5):
            await asyncio.sleep(wait)
            try:
                info = await sandbox.get_info()
                print(f"get_info after {wait}s:", info.status.state, info.status.reason)
            except Exception as e:  # noqa: BLE001 (a probe: whatever it raises is the finding)
                print(f"get_info after {wait}s raised:", described(e))
            print(f"is_healthy after {wait}s:", await sandbox.is_healthy())
            try:
                ran = await sandbox.commands.run("echo again")
                print(f"next command after {wait}s:", ran.exit_code, ran.logs.stdout)
            except Exception as e:  # noqa: BLE001 (a probe: whatever it raises is the finding)
                print(f"next command after {wait}s raised:", described(e))
    finally:
        try:
            await sandbox.kill()
            print("kill via SDK: done")
        except Exception as e:  # noqa: BLE001 (a probe: whatever it raises is the finding)
            print("kill via SDK raised:", described(e))
        await sandbox.close()


asyncio.run(main())
