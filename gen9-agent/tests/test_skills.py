"""Built-in skills follow the Agent Skills specification (agentskills.io/specification), so any
compliant agent could load them, and are read-only to the agent (skills.py)."""

import asyncio
import re

import pytest
import yaml

from gen9_agent import skills
from gen9_agent.definition import GEN9

pytestmark = pytest.mark.asyncio

ROOT = GEN9 / "skills"  # Gen9's agent's skills (definition.py)
NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


async def test_every_builtin_skill_follows_the_specification() -> None:
    folders = sorted(p for p in ROOT.iterdir() if p.is_dir())
    assert folders, "no built-in skills"
    for folder in folders:
        text = await asyncio.to_thread((folder / "SKILL.md").read_text)
        _, frontmatter, body = text.split("---", 2)
        meta = yaml.safe_load(frontmatter)
        assert meta["name"] == folder.name and NAME.match(meta["name"]), folder.name
        assert len(meta["name"]) <= 64
        assert 1 <= len(meta["description"]) <= 1024, folder.name
        assert set(meta) <= {
            "name",
            "description",
            "license",
            "compatibility",
            "metadata",
            "allowed-tools",
        }
        assert all(isinstance(v, str) for v in meta.get("metadata", {}).values())
        assert len(body.splitlines()) < 500, folder.name


async def test_skills_are_read_only_to_the_agent() -> None:
    [rule] = skills.PERMISSIONS
    assert (rule.operations, rule.paths, rule.mode) == (
        ["write"],
        ["/skills/**"],
        "deny",
    )
