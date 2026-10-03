#!/usr/bin/env python3
"""Fail unless Gen9's packages declare one version: one for all of Gen9, a release's tag
(docs/plans/deploy.md, R1). gen9-agent and gen9-cli in pyproject.toml, gen9-ui in package.json.
Run by `make config`, so by CI; the release workflow also checks the tag against it.

    scripts/check-version.py            prints the version
    scripts/check-version.py v0.1.0     also fails unless the tag is v<the version>
"""

import json
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

declared = {
    "gen9-agent": tomllib.loads((ROOT / "gen9-agent/pyproject.toml").read_text())[
        "project"
    ]["version"],
    "gen9-cli": tomllib.loads((ROOT / "gen9-cli/pyproject.toml").read_text())[
        "project"
    ]["version"],
    "gen9-ui": json.loads((ROOT / "gen9-ui/package.json").read_text())["version"],
}
versions = set(declared.values())
if len(versions) != 1:
    print(
        "Gen9's packages declare different versions: "
        + ", ".join(f"{k} {v}" for k, v in declared.items())
    )
    sys.exit(1)
(current,) = versions
if len(sys.argv) > 1 and sys.argv[1] != f"v{current}":
    print(
        f"the tag {sys.argv[1]} isn't v{current}, the version Gen9's packages declare"
    )
    sys.exit(1)
print(f"version: {current}, in gen9-agent, gen9-cli and gen9-ui")
