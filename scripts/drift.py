#!/usr/bin/env python3
"""Fail if what runs differs from what the Compose files, the settings and images.env declare.

Docker (docs/plans/deploy.md, U4). For each stack's services, as `make up` would start them:
- Compose's hash of the service's configuration against the one the container was created with
  (`com.docker.compose.config-hash`): a changed setting, image, command, mount or port;
- the image reference the container runs against the one declared (a release's digest from
  images.env, or a local tag);
- what `docker update` changes without Compose knowing: memory, CPUs, processes, restart policy;
- a service that should run but doesn't, and containers of the project Compose doesn't declare
  (sandboxes' sidecars, which carry the project's labels, aside).
Compose's hash alone missed `docker update` (explore/deploy/NOTES.md, R2a). Not compared: files
changed inside a running container (`docker diff` lists them, among what the service itself
writes as it runs), as `make k8s-diff` doesn't compare a pod's either.

    scripts/drift.py [STACK...]          exit 2 naming each difference (make diff); default: every
                                         stack; reads images.env as make up does
    scripts/drift.py --reset [STACK...]  then puts back what differs (make reset): each such service
                                         recreated as declared, a container not declared removed
"""

import asyncio
import json
import os
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
ALL = [
    "postgres",
    "keycloak",
    "langfuse",
    "temporal",
    "models",
    "sandbox",
    "agent",
    "ui",
    "edge",
]
# Run only once set up (gen9-edge: make setup DOMAIN=…): left out until its .env exists
OPTIONAL = {"edge"}


async def run(
    *command: str, cwd: str = ROOT, env: dict | None = None
) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=cwd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await process.communicate()
    return process.returncode or 0, out.decode()


def images_env() -> dict[str, str]:
    """images.env's variables, as make up reads them; read before the event loop starts."""
    path = os.path.join(ROOT, "images.env")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        pairs = (line.strip().split("=", 1) for line in f if "=" in line)
        return {k: v for k, v in pairs if not k.startswith("#")}


def expected_host(service: dict) -> dict[str, object]:
    """The settings `docker update` can change, as Compose would create them."""
    deploy = (service.get("deploy") or {}).get("resources", {}).get("limits", {})
    memory = service.get("mem_limit") or deploy.get("memory")
    cpus = service.get("cpus") or deploy.get("cpus")
    restart = service.get("restart") or "no"
    return {
        "Memory": int(memory) if memory else 0,
        "NanoCpus": int(float(cpus) * 1e9) if cpus else 0,
        "PidsLimit": service.get("pids_limit") or deploy.get("pids") or None,
        "RestartPolicy": restart.split(":")[0],
    }


async def stack_drift(stack: str, env: dict) -> list[tuple[str, str]] | None:
    """What differs in the stack, as (service or container, what); None if it isn't set up."""
    folder = os.path.join(ROOT, f"gen9-{stack}")
    status, out = await run(
        "docker", "compose", "config", "--format", "json", cwd=folder, env=env
    )
    if status:
        return None
    config = json.loads(out)
    project = config["name"]
    _, hashes = await run(
        "docker", "compose", "config", "--hash", "*", cwd=folder, env=env
    )
    want = dict(line.split(" ", 1) for line in hashes.splitlines() if " " in line)
    _, ids = await run(
        "docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"
    )
    containers = []
    if ids.split():
        _, inspected = await run("docker", "inspect", *ids.split())
        containers = json.loads(inspected)
    problems, seen = [], set()
    for c in containers:
        labels = c["Config"]["Labels"] or {}
        name = c["Name"].lstrip("/")
        service = labels.get("com.docker.compose.service")
        if (
            "opensandbox.io/id" in labels
            or labels.get("com.docker.compose.oneoff") == "True"
        ):
            continue
        if service not in config["services"]:
            problems.append((name, f"not a service of {project}'s Compose file"))
            continue
        seen.add(service)
        spec = config["services"][service]
        if labels.get("com.docker.compose.config-hash") != want.get(service):
            problems.append((service, "its configuration changed since it was created"))
        if c["Config"]["Image"] != spec["image"]:
            problems.append(
                (service, f"runs {c['Config']['Image']}, declared {spec['image']}")
            )
        host, expect = c["HostConfig"], expected_host(spec)
        for key, value in expect.items():
            got = host.get(key)
            if key == "RestartPolicy":
                got = (got or {}).get("Name") or "no"
            if (got or None) != (value or None):
                problems.append((service, f"{key} is {got}, declared {value}"))
        one_shot = spec.get("restart") == "no"
        if not one_shot and c["State"]["Status"] != "running":
            problems.append((service, f"{c['State']['Status']}, not running"))
    # Every service Compose would start, those of the profiles .env lists too (a bundled store)
    for service in config["services"]:
        if service not in seen:
            problems.append((service, "declared, no container"))
    return problems


async def reset(
    stack: str, problems: list[tuple[str, str]], env: dict, how: str
) -> bool:
    """Each service that differs recreated as declared, built or pulled as make up does; a
    container the Compose file doesn't declare removed."""
    folder = os.path.join(ROOT, f"gen9-{stack}")
    _, out = await run("docker", "compose", "config", "--services", cwd=folder, env=env)
    declared = set(out.split())
    services = sorted({who for who, _ in problems if who in declared})
    strays = sorted({who for who, _ in problems if who not in declared})
    ok = True
    if strays:
        status, _ = await run("docker", "rm", "-f", *strays)
        ok = ok and not status
    if services:
        status, _ = await run(
            "docker",
            "compose",
            "up",
            "-d",
            how,
            "--force-recreate",
            "--no-deps",
            "--wait",
            *services,
            cwd=folder,
            env=env,
        )
        ok = ok and not status
    print(
        f"gen9-{stack}: "
        + ("put back" if ok else "not put back")
        + ": "
        + ", ".join(services + strays)
    )
    return ok


async def main(env: dict[str, str], how: str, unset: set[str]) -> int:
    args = sys.argv[1:]
    putting_back = args[:1] == ["--reset"]
    stacks = args[putting_back:] or ALL
    results = await asyncio.gather(
        *(stack_drift(s, env) for s in stacks if s not in unset)
    )
    results = iter(results)
    results = [None if s in unset else next(results) for s in stacks]
    drift = False
    for stack, problems in zip(stacks, results, strict=True):
        if problems is None:
            print(f"gen9-{stack}: not set up, left out")
        elif problems:
            drift = True
            print(f"gen9-{stack}: drift")
            print("\n".join(f"  {who}: {what}" for who, what in problems))
        else:
            print(f"gen9-{stack}: as declared")
    if not putting_back:
        return 2 if drift else 0
    # In make's order, one stack after another: a stack's services may wait on the one before
    for stack, problems in zip(stacks, results, strict=True):
        if problems and not await reset(stack, problems, env, how):
            return 1
    return 0


# Built here, or pulled by digest when images.env exists, as make up does
HOW = "--no-build" if os.path.exists(os.path.join(ROOT, "images.env")) else "--build"
UNSET = {
    s for s in OPTIONAL if not os.path.exists(os.path.join(ROOT, f"gen9-{s}", ".env"))
}
sys.exit(asyncio.run(main({**os.environ, **images_env()}, HOW, UNSET)))
