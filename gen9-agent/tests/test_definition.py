"""Agents defined as folders (definition.py), in Deep Agents' layout: Gen9's own loads whole, its
version follows its files, and a malformed folder is refused."""

import asyncio
import shutil
from pathlib import Path

import pytest

from gen9_agent.definition import GEN9, load, version

pytestmark = pytest.mark.asyncio


async def test_gen9s_own_agent_is_a_folder() -> None:
    agent = await asyncio.to_thread(load, GEN9)
    assert (agent.name, agent.model) == ("gen9", "chat")
    # An AI system, and says so (AI Act Art. 50(1); manual-e2e.md, P5-B1)
    assert agent.instructions.startswith(
        "You are Gen9, a precise general-purpose agent, and an AI system, not a person."
    )
    assert agent.skills_dir == GEN9 / "skills"
    assert await asyncio.to_thread(
        (agent.skills_dir / "research-brief" / "SKILL.md").is_file
    )
    [checker] = agent.subagents
    assert checker.name == "fact-checker" and checker.model is None
    assert "primary source" in checker.instructions
    assert "verify" in checker.description
    assert len(agent.version) == 12


async def test_the_version_follows_the_files(tmp_path: Path) -> None:
    copy = tmp_path / "gen9"
    await asyncio.to_thread(shutil.copytree, GEN9, copy)
    before = await asyncio.to_thread(version, copy)
    assert before == await asyncio.to_thread(
        version, GEN9
    )  # the folder, not where it is
    skill = copy / "skills" / "research-brief" / "SKILL.md"
    await asyncio.to_thread(
        skill.write_text, (await asyncio.to_thread(skill.read_text)) + "\n"
    )
    assert await asyncio.to_thread(version, copy) != before


async def test_a_name_that_isnt_the_folders_is_refused(tmp_path: Path) -> None:
    folder = tmp_path / "helper"
    await asyncio.to_thread(folder.mkdir)
    await asyncio.to_thread(
        (folder / "AGENTS.md").write_text, "---\nname: other\ndescription: x\n---\nHi"
    )
    with pytest.raises(ValueError, match="must be 'helper'"):
        await asyncio.to_thread(load, folder)
