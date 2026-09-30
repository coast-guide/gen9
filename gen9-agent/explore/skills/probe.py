"""Probe: built-in, read-only skills with Deep Agents 0.7.18, as Gen9 would ship them.

Skills in a directory of the package, served at /skills/ by a FilesystemBackend (virtual mode)
routed from a CompositeBackend beside the memory route, `skills=["/skills/"]`, and a permission
denying writes under /skills/. Questions:
1. Does SkillsMiddleware discover a skill through the composite route?
2. When a task matches the description, does the agent read SKILL.md and follow it? And not
   when it doesn't match?
3. Is the skill read-only to the agent?
Run from gen9-agent/: uv run python explore/skills/probe.py
"""

import asyncio
import tempfile
import uuid
from pathlib import Path

import httpx
from deepagents import FilesystemPermission, create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
from langchain_openai import ChatOpenAI

ENV = dict(
    line.split("=", 1)
    for line in Path("models.local.env").read_text().splitlines()
    if "=" in line and not line.startswith("#")
)
SKILL = """---
name: research-brief
description: Write a research brief, a short sourced summary of what is known about a topic with the answer first. Use when the person asks for a brief, a report, or an overview of something.
---
# Research brief

Write the brief in exactly this structure:

## Answer
Two sentences at most.

## Findings
Two bullets.

## Sources
A numbered list. If you did not search, write "None consulted."

End with the line: Brief prepared by the research-brief skill.
"""


def steps(result) -> list[str]:
    return [
        f"{c['name']}({c['args'].get('file_path') or c['args'].get('path') or ''})"
        for m in result["messages"]
        for c in getattr(m, "tool_calls", None) or []
    ]


async def main() -> None:
    with tempfile.TemporaryDirectory() as root:
        (Path(root) / "research-brief").mkdir()
        (Path(root) / "research-brief" / "SKILL.md").write_text(SKILL)
        async with httpx.AsyncClient(timeout=120) as http:
            model = ChatOpenAI(
                model="chat",
                base_url=f"{ENV['GEN9_MODELS_URL']}/v1",
                api_key=ENV["GEN9_MODELS_KEY"],
                http_async_client=http,
                default_headers={"x-litellm-end-user-id": "skills-probe"},
            )
            agent = create_deep_agent(
                model=model,
                system_prompt="You are Gen9, a research assistant. Don't search the web.",
                skills=["/skills/"],
                backend=CompositeBackend(
                    default=StateBackend(),
                    routes={
                        "/skills/": FilesystemBackend(root_dir=root, virtual_mode=True)
                    },
                ),
                permissions=[
                    FilesystemPermission(
                        operations=["write"], paths=["/skills/**"], mode="deny"
                    ),
                ],
            )

            async def ask(text: str):
                return await agent.ainvoke(
                    {"messages": [{"role": "user", "content": text}]},
                    {"configurable": {"thread_id": str(uuid.uuid4())}},
                )

            r = await ask("Give me a brief on what a B-tree is.")
            answer = r["messages"][-1].content
            print("1-2. matching task ->", steps(r))
            print(
                "     followed:",
                all(
                    h in answer
                    for h in (
                        "## Answer",
                        "## Findings",
                        "## Sources",
                        "research-brief skill",
                    )
                ),
            )
            print("     answer head:", answer[:160].replace("\n", " | "))
            r = await ask("What is 17 times 23? Reply with the number only.")
            print("2. unrelated task ->", steps(r), "|", r["messages"][-1].content[:40])
            r = await ask(
                "Edit /skills/research-brief/SKILL.md so the Answer section allows five sentences. "
                "Reply with Done or with why you couldn't."
            )
            print(
                "3. edit the skill ->", steps(r), "|", r["messages"][-1].content[:160]
            )
            print(
                "   file unchanged:",
                (Path(root) / "research-brief" / "SKILL.md").read_text() == SKILL,
            )


asyncio.run(main())
