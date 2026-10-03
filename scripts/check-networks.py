#!/usr/bin/env python3
"""Fail if a stack could reach another stack's container under one of its own service names.

Stacks reach each other over shared networks (gen9-<stack>, declared external). Docker registers
every service's name and aliases on each network the service joins, and a container on two
networks that looks up a name registered on both got the shared network's answer every time
(Docker 29): keycloak asking for `postgres` would reach gen9-postgres, not its own database.
So a name one stack registers on a shared network must not be a service name of another stack
with a service on that network. Run by `make config` and CI; reads each stack's Compose config.

    scripts/check-networks.py [STACK...]      default: postgres keycloak langfuse temporal models sandbox agent ui edge
"""
import json
import os
import subprocess
import sys

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
stacks = sys.argv[1:] or ["postgres", "keycloak", "langfuse", "temporal", "models", "sandbox", "agent", "ui", "edge"]

configs = {}
for stack in stacks:
    result = subprocess.run(
        ["docker", "compose", "--profile", "*", "config", "--format", "json"],
        cwd=f"gen9-{stack}", capture_output=True, text=True,
    )
    if result.returncode == 0:
        configs[stack] = json.loads(result.stdout)
    else:
        print(f"gen9-{stack}: not set up, left out of the check")

# shared network -> stack -> names it registers there; and stack -> its services on the network
registered, joined = {}, {}
for stack, config in configs.items():
    networks = config.get("networks") or {}
    for service, spec in config["services"].items():
        for key, options in (spec.get("networks") or {}).items():
            network = networks.get(key) or {}
            if not network.get("external"):
                continue  # the stack's own project network
            name = network.get("name", key)
            aliases = (options or {}).get("aliases") or []
            registered.setdefault(name, {}).setdefault(stack, set()).update([service, *aliases])
            joined.setdefault(name, {}).setdefault(stack, []).append(service)

clashes = []
for network, by_stack in sorted(registered.items()):
    for owner, names in sorted(by_stack.items()):
        for other in sorted(joined[network]):
            if other == owner:
                continue
            for name in sorted(names & set(configs[other]["services"])):
                clashes.append(
                    f"{network}: gen9-{owner} registers '{name}', which is also a gen9-{other} service; "
                    f"gen9-{other}'s {', '.join(joined[network][other])} on {network} could reach "
                    f"gen9-{owner} when asking for its own '{name}'. Rename one of them."
                )

if clashes:
    print("\n".join(clashes))
    sys.exit(1)
print(f"shared networks: no name clashes ({', '.join(sorted(registered))})")
