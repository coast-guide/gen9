"""Can one backend instance, shared by every run, find the run's thread from LangGraph's config when
a tool calls it? And does a Deep Agent whose CompositeBackend defaults to a sandbox get `execute`,
keep Gen9's permissions on its routes, and offload large results under `artifacts_root`?

    uv run python explore/sandbox/backend_probe.py
"""

import asyncio

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, StateBackend
from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.config import get_config

from gen9_agent import memory, skills

SEEN: list[tuple[str, str]] = []


class Probe(BaseSandbox):
    @property
    def id(self) -> str:
        return "probe"

    def execute(self, command, *, timeout=None):
        raise NotImplementedError("async only")

    async def aexecute(self, command, *, timeout=None):  # noqa: ASYNC109
        thread = get_config().get("configurable", {}).get("thread_id", "?")
        SEEN.append((thread, command.split()[0]))
        return ExecuteResponse(output="42\n", exit_code=0, truncated=False)

    def upload_files(self, files):
        return [FileUploadResponse(path=p, error=None) for p, _ in files]

    def download_files(self, paths):
        return [
            FileDownloadResponse(path=p, content=b"", error="file_not_found")
            for p in paths
        ]


class Scripted(BaseChatModel):
    script: list[AIMessage]
    calls: int = 0
    offered: list[list[str]] = []  # noqa: RUF012

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        message = self.script[self.calls % len(self.script)]
        self.calls += 1
        return ChatResult(generations=[ChatGeneration(message=message)])

    def bind_tools(self, tools, **kwargs):
        self.offered.append([getattr(t, "name", "") for t in tools])
        return self

    @property
    def _llm_type(self) -> str:
        return "scripted"


async def main() -> None:
    model = Scripted(
        script=[
            AIMessage(
                "",
                tool_calls=[
                    {
                        "name": "execute",
                        "args": {"command": "python -c 'print(6*7)'"},
                        "id": "c1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage("Done."),
        ]
    )
    backend = CompositeBackend(
        default=Probe(),
        routes={
            memory.ROUTE: StateBackend(),
            skills.ROUTE: StateBackend(),
            "/gen9/": StateBackend(),
        },
        artifacts_root="/gen9",
    )
    agent = create_deep_agent(
        model=model,
        checkpointer=InMemorySaver(),
        backend=backend,
        permissions=memory.PERMISSIONS + skills.PERMISSIONS,
    )
    for thread in ("thread-a", "thread-b"):
        model.calls = 0
        await agent.ainvoke(
            {"messages": [{"role": "user", "content": "go"}]},
            {"configurable": {"thread_id": thread}},
        )
    print("offered execute:", "execute" in model.offered[0])
    print("seen by the shared backend:", SEEN)


asyncio.run(main())
