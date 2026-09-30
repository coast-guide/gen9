"""Record the stream a Deep Agent run emits (LangGraph v2 stream parts), to design Gen9's run events.

    uv run --env-file .env python explore/harness/stream_shapes.py

Writes explore/out/stream-shapes.jsonl (one line per part, message content truncated) and prints a
summary of part types, namespaces and message/tool shapes.
"""

import asyncio
import json
from collections import Counter
from pathlib import Path

from deepagents import create_deep_agent
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

OUT = Path(__file__).parent.parent / "out" / "stream-shapes.jsonl"


@tool
def lookup(city: str) -> str:
    """Look up a city's population."""
    return {"Paris": "2.1 million", "Tokyo": "14 million"}.get(city, "unknown")


def brief(value, depth=0):
    if depth > 3:
        return "…"
    if isinstance(value, dict):
        return {k: brief(v, depth + 1) for k, v in list(value.items())[:12]}
    if isinstance(value, list | tuple):
        return [brief(v, depth + 1) for v in value[:6]]
    if hasattr(value, "model_dump"):
        return {
            "__type__": type(value).__name__,
            **brief(value.model_dump(), depth + 1),
        }
    if isinstance(value, str):
        return value[:80]
    return (
        value
        if isinstance(value, int | float | bool | type(None))
        else repr(value)[:80]
    )


async def main() -> None:
    agent = create_deep_agent(
        model="openai:gpt-5.5",
        tools=[lookup],
        system_prompt="First write a short todo list with write_todos. Look up both cities with lookup. "
        "Then ask the general-purpose subagent (task tool) to say which is larger in one word. Reply in one line.",
        checkpointer=InMemorySaver(),
    )
    OUT.parent.mkdir(exist_ok=True)
    counts: Counter = Counter()
    with OUT.open("w") as f:
        async for part in agent.astream(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": "Compare Paris and Tokyo by population.",
                    }
                ]
            },
            {"configurable": {"thread_id": "shapes"}},
            stream_mode=["messages", "updates", "custom"],
            subgraphs=True,
            version="v2",
        ):
            ns = part.get("ns") if isinstance(part, dict) else None
            kind = part.get("type") if isinstance(part, dict) else type(part).__name__
            counts[(kind, "sub" if ns else "root")] += 1
            f.write(json.dumps(brief(part), default=str) + "\n")
    for key, n in sorted(counts.items()):
        print(key, n)
    print("wrote", OUT)


asyncio.run(main())
