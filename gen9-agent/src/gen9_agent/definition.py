"""Agents defined as folders, in Deep Agents' own layout (docs.langchain.com, Deep Agents Code,
"Data locations" and "Use subagents"):

    <agent>/
    ├── AGENTS.md                      frontmatter: name, description, model (a router alias);
    │                                  the body is the agent's instructions
    ├── skills/<skill>/SKILL.md        the agent's skills (skills.py)
    └── agents/<subagent>/AGENTS.md    frontmatter: name, description, optional model; the body is
                                       the subagent's instructions

An agent's version is a hash of every file in its folder, recorded on each run it answers, so an
answer can be traced to the exact definition behind it. Loading reads files: call `load` in a
thread (`asyncio.to_thread`), as `runtime.open_runtime` does.
"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

# Gen9's own agent, shipped in this package
GEN9 = Path(__file__).with_name("agents") / "gen9"
# Agent and subagent names, as the Agent Skills specification names skills
_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


@dataclass(frozen=True)
class SubAgentSpec:
    name: str
    description: str
    instructions: str
    model: str | None  # a router alias; None: the agent's model


@dataclass(frozen=True)
class AgentDefinition:
    name: str
    description: str
    model: str  # a router alias (gen9-models/config.yaml)
    instructions: str
    skills_dir: Path
    # Its skills (name, description): a plugin's skill can't take one's name (plugin_skills.py)
    skills: tuple[tuple[str, str], ...]
    subagents: tuple[SubAgentSpec, ...]
    version: str  # the first 12 hex of the folder's SHA-256

    @property
    def skill_names(self) -> frozenset[str]:
        return frozenset(name for name, _ in self.skills)


def _parse(path: Path, folder_name: str) -> tuple[dict, str]:
    text = path.read_text()
    if not text.startswith("---"):
        raise ValueError(f"{path}: no YAML frontmatter")
    _, frontmatter, body = text.split("---", 2)
    meta = yaml.safe_load(frontmatter) or {}
    name = meta.get("name")
    if name != folder_name or not _NAME.match(str(name)):
        raise ValueError(
            f"{path}: name {name!r} must be {folder_name!r}, lowercase and hyphens"
        )
    if not str(meta.get("description") or "").strip():
        raise ValueError(f"{path}: a description is required")
    return meta, body.strip()


def version(folder: Path) -> str:
    digest = hashlib.sha256()
    for file in sorted(p for p in folder.rglob("*") if p.is_file()):
        digest.update(file.relative_to(folder).as_posix().encode() + b"\0")
        digest.update(file.read_bytes() + b"\0")
    return digest.hexdigest()[:12]


def load(folder: Path) -> AgentDefinition:
    meta, instructions = _parse(folder / "AGENTS.md", folder.name)
    subagents = tuple(
        SubAgentSpec(
            name=sub_meta["name"],
            description=sub_meta["description"].strip(),
            instructions=body,
            model=sub_meta.get("model"),
        )
        for sub in sorted((folder / "agents").glob("*/AGENTS.md"))
        for sub_meta, body in [_parse(sub, sub.parent.name)]
    )
    return AgentDefinition(
        name=meta["name"],
        description=meta["description"].strip(),
        model=meta.get("model") or "chat",
        instructions=instructions,
        skills_dir=folder / "skills",
        skills=tuple(
            (meta["name"], str(meta["description"]).strip())
            for skill in sorted((folder / "skills").glob("*/SKILL.md"))
            for meta, _ in [_parse(skill, skill.parent.name)]
        ),
        subagents=subagents,
        version=version(folder),
    )
