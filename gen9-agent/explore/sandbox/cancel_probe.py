"""Does a command stop when the call running it is cancelled? (NOTES.md)

Needs this folder's server (compose.yaml). In a new sandbox it runs `sleep 60; echo late` from a
task, cancels the task 3 s in, and looks for the `sleep` in the container 1, 5 and 20 s later:
first with the cancellation alone (the SDK's stream closed), then interrupting the command by its
id (`commands.interrupt`, the id from the stream's `init` event) when cancelled.

    uv run --no-project --with opensandbox==1.1.0 python cancel_probe.py
"""

import asyncio
import subprocess
from datetime import timedelta

from opensandbox.config import ConnectionConfig
from opensandbox.models.execd import ExecutionHandlers, ExecutionInit
from opensandbox.sandbox import Sandbox

FIND = (
    'for p in /proc/[0-9]*; do a=$(tr "\\0" " " < $p/cmdline 2>/dev/null); '
    'case "$a" in "sleep 60"*|*"sleep 60;"*) echo "${p#/proc/} $a";; esac; done'
)


async def docker(*args: str) -> str:
    # The docker CLI has no async API: in a thread
    result = await asyncio.to_thread(
        subprocess.run, ["docker", *args], capture_output=True, text=True, check=False
    )
    return (result.stdout + result.stderr).strip()


async def case(sandbox: Sandbox, container: str, interrupt: bool) -> None:
    ids: list[str] = []

    async def on_init(init: ExecutionInit) -> None:
        ids.append(init.id)

    async def run() -> None:
        try:
            await sandbox.commands.run(
                "sleep 60; echo late",
                handlers=ExecutionHandlers(on_init=on_init),
            )
        except asyncio.CancelledError:
            if interrupt and ids:
                # What Gen9 would do: the interrupt must outlive the cancellation
                await asyncio.shield(sandbox.commands.interrupt(ids[0]))
                print("  interrupted", ids[0])
            raise

    print("interrupt on cancel:" if interrupt else "cancellation alone:")
    task = asyncio.create_task(run())
    await asyncio.sleep(3)
    print(
        "  running before cancel:",
        await docker("exec", container, "sh", "-c", FIND) or "-",
    )
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    waited = 0
    for at in (1, 5, 20):
        await asyncio.sleep(at - waited)
        waited = at
        found = await docker("exec", container, "sh", "-c", FIND)
        print(f"  {at:>2} s after cancel:", found or "gone")


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
        names = await docker("ps", "--format", "{{.Names}}")
        container = next(
            n for n in names.splitlines() if sandbox.id in n and "egress" not in n
        )
        await case(sandbox, container, interrupt=False)
        # What the first case left running must not count in the second
        await docker(
            "exec",
            container,
            "sh",
            "-c",
            f"for p in $({FIND} | cut -d' ' -f1); do kill -9 $p; done",
        )
        await case(sandbox, container, interrupt=True)
    finally:
        await sandbox.kill()
        await sandbox.close()


asyncio.run(main())
