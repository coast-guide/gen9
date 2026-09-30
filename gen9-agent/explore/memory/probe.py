"""Probe: per-user long-term memory with Deep Agents 0.7.18, as Gen9 would run it.

`/memories/AGENTS.md` routed by a CompositeBackend to a StoreBackend on AsyncPostgresStore, its
namespace from the run's context (Gen9 runs its own server, so there is no `server_info`), and
`memory=` loading it into the prompt. Questions:
1. With no file yet, does the agent create it when told to remember something, and with which tool?
2. Does a new chat of the same user use it? Does another user's chat not see it?
3. Does the run context reach a subagent (the `task` tool), so its memory writes land per user?
4. What does the stored item look like (for an API that shows it)? Does deleting it forget?
Round 2 (after round 1 saw the agent invent `/memories/user_preferences.txt`): a starter
`/memories/AGENTS.md` per user, and permissions allowing writes to it alone under /memories/.

Run: docker compose -f explore/memory/compose.yaml up -d --wait, then from gen9-agent/:
  uv run python explore/memory/probe.py
Models go through gen9-models by alias (models.local.env); prints what it observed, no secrets.
"""

import asyncio
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx
from deepagents import FilesystemPermission, create_deep_agent
from deepagents.backends import CompositeBackend, StateBackend, StoreBackend
from deepagents.backends.utils import create_file_data
from langchain_openai import ChatOpenAI
from langgraph.store.postgres.aio import AsyncPostgresStore

ENV = dict(
    line.split("=", 1)
    for line in Path("models.local.env").read_text().splitlines()
    if "=" in line and not line.startswith("#")
)
DSN = "postgresql://postgres:exp@127.0.0.1:19094/postgres"


@dataclass
class Gen9Context:
    user_sub: str


def namespace(rt) -> tuple[str, ...]:
    return ("memories", rt.context.user_sub)


STARTER = "# What Gen9 remembers about this person\n"


async def ask(agent, user: str, text: str, store=None) -> tuple[str, list[str]]:
    thread = str(uuid.uuid4())
    if store is not None and await store.aget(("memories", user), "/AGENTS.md") is None:
        await store.aput(("memories", user), "/AGENTS.md", create_file_data(STARTER))
    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": text}]},
        {"configurable": {"thread_id": thread}},
        context=Gen9Context(user_sub=user),
    )
    tools = [
        f"{c['name']}({c['args'].get('file_path') or c['args'].get('subagent_type') or ''})"
        for m in result["messages"]
        for c in getattr(m, "tool_calls", None) or []
    ]
    return result["messages"][-1].content if isinstance(
        result["messages"][-1].content, str
    ) else str(result["messages"][-1].content), tools


async def main() -> None:
    async with (
        AsyncPostgresStore.from_conn_string(DSN) as store,
        httpx.AsyncClient(timeout=120) as http,
    ):
        await store.setup()
        model = ChatOpenAI(
            model="chat",
            base_url=f"{ENV['GEN9_MODELS_URL']}/v1",
            api_key=ENV["GEN9_MODELS_KEY"],
            http_async_client=http,
            default_headers={"x-litellm-end-user-id": "memory-probe"},
        )
        agent = create_deep_agent(
            model=model,
            system_prompt="You are Gen9, a research assistant. Answer in one short sentence.",
            memory=["/memories/AGENTS.md"],
            backend=CompositeBackend(
                default=StateBackend(),
                routes={"/memories/": StoreBackend(namespace=namespace)},
            ),
            store=store,
            context_schema=Gen9Context,
            # First match wins: the one memory file may change, nothing else under /memories/
            permissions=[
                FilesystemPermission(
                    operations=["write"], paths=["/memories/AGENTS.md"], mode="allow"
                ),
                FilesystemPermission(
                    operations=["write"], paths=["/memories/**"], mode="deny"
                ),
            ],
        )
        a, b = "user-a-" + uuid.uuid4().hex[:6], "user-b-" + uuid.uuid4().hex[:6]

        answer, tools = await ask(
            agent, a, "Remember that my favourite colour is teal.", store
        )
        print("1. A: remember teal ->", tools, "|", answer[:100])
        items = await store.asearch(("memories", a))
        print(
            "   store items for A:",
            [(i.namespace, i.key, sorted(i.value)) for i in items],
        )
        if items:
            print(
                "   value:",
                items[0].value,
            )

        answer, tools = await ask(agent, a, "What is my favourite colour?", store)
        print("2. A, new chat ->", tools, "|", answer[:120])
        answer, tools = await ask(agent, b, "What is my favourite colour?", store)
        print("   B, new chat ->", tools, "|", answer[:120])
        print("   store items for B:", len(await store.asearch(("memories", b))))

        answer, tools = await ask(
            agent,
            a,
            "Use the general-purpose subagent (the task tool) to add to my memory file that I "
            "prefer metric units. Don't edit the file yourself.",
            store,
        )
        print("3. A, via a subagent ->", tools, "|", answer[:100])
        items = await store.asearch(("memories", a))
        text = "\n".join(str(i.value.get("content")) for i in items)
        print("   A's items:", [i.key for i in items])
        print("   A's memory now mentions metric:", "metric" in text.lower())
        print(
            "   namespaces in the store:",
            sorted({i.namespace for i in await store.asearch(("memories",), limit=50)}),
        )

        await store.adelete(("memories", a), "/AGENTS.md")
        print(
            "4. deleted; A's items:",
            [i.key for i in await store.asearch(("memories", a))],
        )
        answer, tools = await ask(
            agent, a, "What is my favourite colour?"
        )  # no starter again
        print("   A, after delete ->", tools, "|", answer[:120])

        answer, tools = await ask(
            agent, a, "Save my shoe size, 44, in a new file /memories/sizes.md.", store
        )
        print("5. A asks for another memory file ->", tools, "|", answer[:140])
        print("   A's items:", [i.key for i in await store.asearch(("memories", a))])


asyncio.run(main())
