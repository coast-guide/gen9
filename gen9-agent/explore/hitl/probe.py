"""Probe: how a Deep Agent's interrupts look in Gen9's stream, and how resuming behaves, on the
real libraries (deepagents 0.7.18, langgraph 1.2.12) with a Postgres checkpointer. A scripted model
makes every run the same.

    docker run -d --rm --name gen9-probe-hitl-pg -e POSTGRES_PASSWORD=probe \
      -p 127.0.0.1:15998:5432 postgres:17
    uv run python explore/hitl/probe.py

Scenarios:
  1. a question (a tool that calls `interrupt()`) in the main agent: the stream part, the id, the
     state's interrupts, and resuming by id
  2. an approval (`interrupt_on`): the request, and a resume repeated after a crash mid-way, to see
     whether the approved tool runs twice
  3. a question inside a subagent (`task`): its namespace and id, and resuming it from the top
  4. two questions in one model message: two interrupts, one resume with both answers
  5. a new message on a thread whose question was never answered
"""

import asyncio
import importlib.metadata as md
import uuid
from typing import Any

from deepagents import create_deep_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command, interrupt
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

DSN = "postgresql://postgres:probe@127.0.0.1:15998/postgres"


class Scripted(BaseChatModel):
    """Replies with the next message of its script, whatever it is asked."""

    script: list[AIMessage]
    calls: int = 0

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        message = self.script[self.calls]
        self.calls += 1
        return ChatResult(generations=[ChatGeneration(message=message)])

    def bind_tools(self, tools, **kwargs):
        return self

    @property
    def _llm_type(self) -> str:
        return "scripted"


EXECUTED: list[str] = []


@tool
def ask_user(question: str) -> str:
    """Ask the person a question and wait for the answer."""
    answer = interrupt({"type": "ask_user", "questions": [{"question": question}]})
    return f"The person answered: {answer['answers'][0]}"


@tool
def send_note(text: str) -> str:
    """Send a note to someone (outward-facing)."""
    EXECUTED.append(text)
    return f"sent: {text}"


def call(name: str, args: dict[str, Any], id: str) -> dict[str, Any]:
    return {"name": name, "args": args, "id": id, "type": "tool_call"}


async def stream(agent, input, config, stop_after_tool: str | None = None) -> list:
    """Stream like gen9-agent's executor; print what matters. Returns the interrupts seen."""
    seen = []
    async for part in agent.astream(
        input,
        config,
        stream_mode=["messages", "updates"],
        subgraphs=True,
        version="v2",
    ):
        if part["type"] != "updates":
            continue
        data = part["data"]
        for node, update in data.items():
            if node == "__interrupt__":
                for i in update:
                    print(f"   interrupt ns={part['ns']} id={i.id} value={i.value}")
                    seen.append(i)
            else:
                msgs = update.get("messages") if isinstance(update, dict) else None
                kinds = [
                    type(m).__name__ for m in (msgs if isinstance(msgs, list) else [])
                ]
                print(f"   update ns={part['ns']} node={node} messages={kinds}")
                if stop_after_tool and any(
                    getattr(m, "name", None) == stop_after_tool
                    for m in (msgs if isinstance(msgs, list) else [])
                ):
                    print("   (simulated crash: the stream stops here)")
                    return seen
    return seen


async def pending(agent, config) -> list:
    state = await agent.aget_state(config, subgraphs=True)
    return [
        (i.id, i.value.get("type") if isinstance(i.value, dict) else i.value)
        for i in state.interrupts
    ]


async def main() -> None:
    print("deepagents", md.version("deepagents"), "langgraph", md.version("langgraph"))
    async with AsyncConnectionPool(
        DSN, kwargs={"autocommit": True, "row_factory": dict_row}, open=False
    ) as pool:
        saver = AsyncPostgresSaver(pool)
        await saver.setup()

        def agent_with(script, **kw):
            model = Scripted(script=script)
            return create_deep_agent(model=model, checkpointer=saver, **kw), model

        # 1. A question in the main agent
        print("\n1. question in the main agent")
        agent, model = agent_with(
            [
                AIMessage(
                    "",
                    tool_calls=[call("ask_user", {"question": "Which colour?"}, "c1")],
                ),
                AIMessage("You chose blue."),
            ],
            tools=[ask_user],
        )
        config = {
            "configurable": {"thread_id": str(uuid.uuid4())},
            "metadata": {"run_id": "r1"},
        }
        seen = await stream(
            agent, {"messages": [{"role": "user", "content": "hi"}]}, config
        )
        print("   pending:", await pending(agent, config))
        await stream(agent, Command(resume={seen[0].id: {"answers": ["blue"]}}), config)
        state = await agent.aget_state(config)
        print(
            "   final:",
            state.values["messages"][-1].text,
            "| pending:",
            await pending(agent, config),
            "| model calls:",
            model.calls,
        )

        # 2. An approval, and a resume repeated after a crash
        print("\n2. approval, resume repeated after a crash")
        EXECUTED.clear()
        agent, model = agent_with(
            [
                AIMessage("", tool_calls=[call("send_note", {"text": "hello"}, "c2")]),
                AIMessage("Sent."),
            ],
            tools=[send_note],
            interrupt_on={"send_note": {"allowed_decisions": ["approve", "reject"]}},
        )
        config = {
            "configurable": {"thread_id": str(uuid.uuid4())},
            "metadata": {"run_id": "r2"},
        }
        seen = await stream(
            agent, {"messages": [{"role": "user", "content": "send hello"}]}, config
        )
        resume = Command(resume={seen[0].id: {"decisions": [{"type": "approve"}]}})
        await stream(agent, resume, config, stop_after_tool="send_note")
        print(
            "   executed after the crash:",
            EXECUTED,
            "| pending:",
            await pending(agent, config),
        )
        await stream(agent, resume, config)
        state = await agent.aget_state(config)
        print(
            "   final:",
            state.values["messages"][-1].text,
            "| executed:",
            EXECUTED,
            "| model calls:",
            model.calls,
        )

        # 3. A question inside a subagent
        print("\n3. question inside a subagent")
        sub_model = Scripted(
            script=[
                AIMessage(
                    "", tool_calls=[call("ask_user", {"question": "Which city?"}, "s1")]
                ),
                AIMessage("The city is Paris."),
            ]
        )
        agent, model = agent_with(
            [
                AIMessage(
                    "",
                    tool_calls=[
                        call(
                            "task",
                            {"description": "find the city", "subagent_type": "helper"},
                            "c3",
                        )
                    ],
                ),
                AIMessage("Paris it is."),
            ],
            subagents=[
                {
                    "name": "helper",
                    "description": "helps",
                    "system_prompt": "help",
                    "model": sub_model,
                    "tools": [ask_user],
                }
            ],
        )
        config = {
            "configurable": {"thread_id": str(uuid.uuid4())},
            "metadata": {"run_id": "r3"},
        }
        seen = await stream(
            agent, {"messages": [{"role": "user", "content": "city?"}]}, config
        )
        print("   pending:", await pending(agent, config))
        await stream(
            agent, Command(resume={seen[-1].id: {"answers": ["Paris"]}}), config
        )
        state = await agent.aget_state(config)
        print(
            "   final:",
            state.values["messages"][-1].text,
            "| pending:",
            await pending(agent, config),
        )

        # 4. Two questions in one message
        print("\n4. two questions at once")
        agent, model = agent_with(
            [
                AIMessage(
                    "",
                    tool_calls=[
                        call("ask_user", {"question": "Colour?"}, "c4a"),
                        call("ask_user", {"question": "Size?"}, "c4b"),
                    ],
                ),
                AIMessage("Blue, large."),
            ],
            tools=[ask_user],
        )
        config = {
            "configurable": {"thread_id": str(uuid.uuid4())},
            "metadata": {"run_id": "r4"},
        }
        seen = await stream(
            agent, {"messages": [{"role": "user", "content": "order"}]}, config
        )
        print("   pending:", await pending(agent, config))
        # Answer only the first: does the run go on, or wait for the second?
        partial = await stream(
            agent, Command(resume={seen[0].id: {"answers": ["blue"]}}), config
        )
        print(
            "   after answering one, interrupts streamed:",
            [i.id for i in partial],
            "| pending:",
            await pending(agent, config),
        )
        await stream(
            agent, Command(resume={seen[1].id: {"answers": ["large"]}}), config
        )
        state = await agent.aget_state(config)
        tool_texts = [
            m.text
            for m in state.values["messages"]
            if type(m).__name__ == "ToolMessage"
        ]
        print(
            "   final:",
            state.values["messages"][-1].text,
            "| tool results:",
            tool_texts,
        )

        # 5. A new message while a question is unanswered
        print("\n5. a new message on a thread with an unanswered question")
        agent, model = agent_with(
            [
                AIMessage(
                    "",
                    tool_calls=[call("ask_user", {"question": "Which colour?"}, "c5")],
                ),
                AIMessage("OK, forget the colour: here is the weather."),
            ],
            tools=[ask_user],
        )
        thread = str(uuid.uuid4())
        config = {"configurable": {"thread_id": thread}, "metadata": {"run_id": "r5"}}
        await stream(agent, {"messages": [{"role": "user", "content": "hi"}]}, config)
        config = {"configurable": {"thread_id": thread}, "metadata": {"run_id": "r5b"}}
        seen = await stream(
            agent,
            {"messages": [{"role": "user", "content": "never mind, weather?"}]},
            config,
        )
        state = await agent.aget_state(config)
        print(
            "   interrupts on the new message:",
            [i.id for i in seen],
            "| pending:",
            await pending(agent, config),
        )
        print(
            "   messages:",
            [
                (type(m).__name__, (m.text or str(getattr(m, "tool_calls", "")))[:70])
                for m in state.values["messages"]
            ],
        )


asyncio.run(main())
