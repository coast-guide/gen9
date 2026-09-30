"""Probe: approvals that depend on the run, not on the compiled agent. Deep Agents' `interrupt_on`
with a `when` predicate that reads the run's context (a permission mode), on the real libraries
(deepagents 0.7.18, langchain 1.4.2) with a scripted model. Nothing leaves this process.

    uv run python explore/hitl/approval_probe.py

Scenarios:
  1. mode "auto": a write runs without asking
  2. mode "ask": the write pauses; what the stream and the interrupt look like
  3. reject with a message: what the model gets back, and whether the file changed
  4. approve: the file is written
"""

import asyncio
import uuid
from dataclasses import dataclass

from deepagents import create_deep_agent
from langchain.agents.middleware import ToolCallRequest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command


@dataclass
class Ctx:
    permission_mode: str


class Scripted(BaseChatModel):
    script: list[AIMessage]
    calls: int = 0

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        message = self.script[self.calls % len(self.script)]
        self.calls += 1
        return ChatResult(generations=[ChatGeneration(message=message)])

    def bind_tools(self, tools, **kwargs):
        return self

    @property
    def _llm_type(self) -> str:
        return "scripted"


def asks_first(request: ToolCallRequest) -> bool:
    return request.runtime.context.permission_mode == "ask"


WRITE = AIMessage(
    "",
    tool_calls=[
        {
            "name": "write_file",
            "args": {"file_path": "/notes.md", "content": "teal"},
            "id": "w1",
            "type": "tool_call",
        }
    ],
)


async def run(mode: str, decision: dict | None) -> None:
    agent = create_deep_agent(
        model=Scripted(script=[WRITE, AIMessage("Done.")]),
        checkpointer=InMemorySaver(),
        context_schema=Ctx,
        interrupt_on={
            "write_file": {
                "allowed_decisions": ["approve", "reject"],
                "when": asks_first,
            }
        },
    )
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    kinds = []
    async for part in agent.astream(
        {"messages": [{"role": "user", "content": "remember teal"}]},
        config,
        context=Ctx(permission_mode=mode),
        stream_mode=["updates"],
        version="v2",
    ):
        kinds += [k for k in part["data"]]
    state = await agent.aget_state(config)
    print(f"  mode={mode}: stream nodes {kinds}")
    if state.interrupts:
        value = state.interrupts[0].value
        print(
            "  interrupt:", {k: value[k] for k in ("action_requests", "review_configs")}
        )
        async for _ in agent.astream(
            Command(resume={state.interrupts[0].id: {"decisions": [decision]}}),
            config,
            context=Ctx(permission_mode=mode),
            stream_mode=["updates"],
            version="v2",
        ):
            pass
        state = await agent.aget_state(config)
    tool = next(m for m in state.values["messages"] if isinstance(m, ToolMessage))
    print(f"  tool result ({tool.status}): {tool.text[:140]}")
    print("  /notes.md written:", "/notes.md" in (state.values.get("files") or {}))


async def main() -> None:
    print("1. auto")
    await run("auto", None)
    print("2-3. ask, then reject with a message")
    await run("ask", {"type": "reject", "message": "Don't save that; just tell me."})
    print("4. ask, then approve")
    await run("ask", {"type": "approve"})


asyncio.run(main())
