"""Approvals (approvals.py): which calls wait for Allow or Deny, how decisions are checked, and one
compiled agent pausing or not by the run's permission mode."""

import uuid
from types import SimpleNamespace

import pytest
from deepagents import create_deep_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from gen9_agent.approvals import INTERRUPT_ON, asks_first, check_decisions, declined
from gen9_agent.memory import MEMORY_PATH, Gen9Context

REQUEST = {
    "action_requests": [{"name": "edit_file", "args": {"file_path": MEMORY_PATH}}],
    "review_configs": [
        {"action_name": "edit_file", "allowed_decisions": ["approve", "reject"]}
    ],
}


def call(name: str, path: str, mode: str) -> SimpleNamespace:
    return SimpleNamespace(
        tool_call={"name": name, "args": {"file_path": path}, "id": "c1"},
        runtime=SimpleNamespace(
            context=Gen9Context(user_sub="s", permission_mode=mode)
        ),
    )


def test_only_memory_writes_wait_and_only_when_the_chat_asks_first() -> None:
    assert asks_first(call("edit_file", MEMORY_PATH, "ask"))
    assert asks_first(call("write_file", MEMORY_PATH, "ask"))
    assert not asks_first(call("edit_file", MEMORY_PATH, "auto"))
    assert not asks_first(
        call("write_file", "/notes.md", "ask")
    )  # the agent's own scratch
    assert not asks_first(call("read_file", MEMORY_PATH, "ask"))


@pytest.mark.asyncio
async def test_deleting_the_memory_waits_as_writing_it_does() -> None:
    # Deep Agents' `delete` went unasked: the agent deleted the memory in "ask" mode (M9, F18)
    assert asks_first(call("delete", MEMORY_PATH, "ask"))
    assert not asks_first(call("delete", MEMORY_PATH, "auto"))
    assert not asks_first(
        call("delete", "/work/out/draft.md", "ask")
    )  # its own working files
    assert "delete" in INTERRUPT_ON


@pytest.mark.asyncio
async def test_every_file_tool_that_changes_something_is_one_the_gate_sees() -> None:
    # A Deep Agents release that adds a file tool which writes must fail here, not go unasked
    from deepagents.middleware.filesystem import _FILE_MUTATION_TOOLS

    assert set(_FILE_MUTATION_TOOLS) <= set(INTERRUPT_ON)


def test_a_command_in_the_environment_waits_when_the_chat_asks_first() -> None:
    assert asks_first(call("execute", "", "ask"))
    assert not asks_first(call("execute", "", "auto"))
    assert "execute" in INTERRUPT_ON


def test_decisions_are_one_per_action_approve_or_reject() -> None:
    assert check_decisions(REQUEST, [{"type": "approve", "message": "ignored"}]) == [
        {"type": "approve"}
    ]
    assert check_decisions(REQUEST, [{"type": "reject", "message": "  not now "}]) == [
        {"type": "reject", "message": "not now"}
    ]
    assert check_decisions(REQUEST, [{"type": "reject"}]) == [{"type": "reject"}]


@pytest.mark.parametrize(
    ("decisions", "why"),
    [
        ([], "expected 1 decisions"),
        ([{"type": "edit"}], "must be approve or reject"),
        ([{"type": "respond", "message": "x"}], "must be approve or reject"),
        ([{"type": "reject", "message": "x" * 2001}], "longer than"),
    ],
)
def test_decisions_that_dont_fit_are_refused(decisions: list[dict], why: str) -> None:
    with pytest.raises(ValueError, match=why):
        check_decisions(REQUEST, decisions)


def test_a_declined_call_is_told_apart_from_a_failure() -> None:
    assert declined(
        "error", "User rejected the tool call for `edit_file` with reason: no"
    )
    assert not declined("error", "Error: file not found")
    assert not declined("success", "User rejected the tool call")


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


WRITE = AIMessage(
    "",
    tool_calls=[
        {
            "name": "write_file",
            "args": {"file_path": MEMORY_PATH, "content": "teal"},
            "id": "w1",
            "type": "tool_call",
        }
    ],
)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "ask"])
async def test_one_agent_pauses_by_the_runs_mode(mode: str) -> None:
    agent = create_deep_agent(
        model=Scripted(script=[WRITE, AIMessage("Done.")]),
        checkpointer=InMemorySaver(),
        context_schema=Gen9Context,
        interrupt_on=INTERRUPT_ON,
    )
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    context = Gen9Context(user_sub="s", permission_mode=mode)
    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "remember teal"}]},
        config,
        context=context,
    )
    if mode == "auto":
        assert "__interrupt__" not in result
        return
    [pending] = result["__interrupt__"]
    assert pending.value["action_requests"][0]["args"]["content"] == "teal"
    done = await agent.ainvoke(
        Command(
            resume={pending.id: {"decisions": [{"type": "reject", "message": "no"}]}}
        ),
        config,
        context=context,
    )
    [told] = [m for m in done["messages"] if isinstance(m, ToolMessage)]
    assert declined(told.status, told.text) and told.text.endswith("with reason: no")
    assert MEMORY_PATH not in (done.get("files") or {})
