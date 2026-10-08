#!/usr/bin/env python3
"""Fail if docker-bake.hcl and the Compose files disagree on how a Gen9 image is built.

Development builds each image from its stack's compose.yaml (`build:`); CI builds the same images
from docker-bake.hcl and pushes them for every deployment (docs/plans/deploy.md, U1). Both must
build the same image from the same context and Dockerfile, and bake must build every image a stack
builds. A service only in the `dev` profile (gen9-ui's dev server) is development's own. Run by
`make config` and CI; reads each stack's Compose config, so a stack not set up is left out.

    scripts/check-images.py
"""

import asyncio
import json
import os
import re
import sys
from pathlib import PurePath

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
STACKS = ["agent", "ui", "keycloak", "postgres", "sandbox"]


async def run(*command: str, cwd: str) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await process.communicate()
    return process.returncode or 0, out.decode()


async def compose_builds(stack: str) -> dict[str, tuple[str, str]] | None:
    """Image name to (context relative to the repository, Dockerfile), or None if not set up."""
    folder = os.path.join(ROOT, f"gen9-{stack}")
    status, out = await run(
        "docker", "compose", "--profile", "*", "config", "--format", "json", cwd=folder
    )
    if status:
        return None
    builds = {}
    for service in json.loads(out)["services"].values():
        if "build" not in service or "dev" in (service.get("profiles") or []):
            continue
        # gen9-agent:dev here, or <registry>/gen9-agent@sha256:… from a lock (scripts/images.sh)
        name = re.split(r"[:@]", service["image"].rsplit("/", 1)[-1])[0]
        context = PurePath(service["build"]["context"]).relative_to(ROOT).as_posix()
        builds[name] = (context, service["build"].get("dockerfile", "Dockerfile"))
    return builds


async def main() -> int:
    (status, out), *stacks = await asyncio.gather(
        run("docker", "buildx", "bake", "--print", cwd=ROOT),
        *(compose_builds(stack) for stack in STACKS),
    )
    if status:
        print("docker buildx bake --print failed: is docker-bake.hcl valid?")
        return 1
    bake = {
        name: (target["context"], target.get("dockerfile", "Dockerfile"))
        for name, target in json.loads(out)["target"].items()
    }
    problems, checked = [], []
    for stack, builds in zip(STACKS, stacks, strict=True):
        if builds is None:
            print(f"gen9-{stack}: not set up, left out of the check")
            continue
        checked.append(f"gen9-{stack}")
        mine = {n: b for n, b in bake.items() if b[0].split("/")[0] == f"gen9-{stack}"}
        for name in sorted(builds.keys() | mine.keys()):
            if name not in mine:
                problems.append(
                    f"gen9-{stack} builds {name}, which docker-bake.hcl doesn't"
                )
            elif name not in builds:
                problems.append(
                    f"docker-bake.hcl builds {name}, which gen9-{stack} doesn't"
                )
            elif builds[name] != mine[name]:
                problems.append(
                    f"{name}: Compose builds {builds[name]}, docker-bake.hcl {mine[name]}"
                )
    if problems:
        print("\n".join(problems))
        return 1
    print(f"images: docker-bake.hcl builds what Compose builds ({', '.join(checked)})")
    return 0


sys.exit(asyncio.run(main()))
