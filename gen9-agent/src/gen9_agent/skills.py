"""An agent's skills (the Agent Skills format: agentskills.io/specification), served read-only at
`/skills/` from the `skills/` folder of its definition (definition.py). Deep Agents'
`SkillsMiddleware` puts each skill's name and description in the prompt, and the agent reads a
skill's `SKILL.md` when a task matches it (explore/skills/NOTES.md). People and the agent can't
change them.
"""

from pathlib import Path

from deepagents import FilesystemPermission
from deepagents.backends import FilesystemBackend

ROUTE = "/skills/"
PERMISSIONS = [
    FilesystemPermission(operations=["write"], paths=[f"{ROUTE}**"], mode="deny")
]


def backend(skills_dir: Path) -> FilesystemBackend:
    """The agent's skills folder at ROUTE. Resolving it touches the disk: build it in a thread
    (runtime.open_runtime). Its reads then run in threads too (the protocol's async methods use
    asyncio.to_thread)."""
    return FilesystemBackend(root_dir=skills_dir, virtual_mode=True)
