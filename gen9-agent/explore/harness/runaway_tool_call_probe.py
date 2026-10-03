"""A model that runs away inside a tool call: how long it holds the worker's event loop.

    uv run python explore/harness/runaway_tool_call_probe.py [CHARS] [--fixed]

A Deep Agent (deepagents, as Gen9's worker builds one) streamed as the worker streams it (LangGraph
v2 parts, messages and updates, subgraphs), on a fake model whose first turn calls `task` with
arguments that start well, then turn into CHARS characters of rambling inside an unterminated JSON
key: the shape a real model produced on k3d (NOTES.md, "A runaway tool call stalls the worker").
Prints the longest the event loop went without running a 0.1 s tick, and each parse of the
arguments by langchain-core's `parse_partial_json` with its time. --fixed first installs Gen9's
linear `parse_partial_json` (gen9_agent/partial_json.py).
"""

import asyncio
import sys
import time
from typing import Any

from deepagents import create_deep_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessageChunk, ai
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langgraph.checkpoint.memory import InMemorySaver

HEAD = (
    '{"subagent_type":"general-purpose","description":"Verify this exact claim using reliable'
    ' primary sources. Return a single verdict line only.","}  to=functions.web_search  ?  '
)
RAMBLE = "Maybe the tool call did not register? Need invoke again. Let's call. Hmm.\n"


class Runaway(BaseChatModel):
    """First turn: the runaway `task` call, in 6-character deltas. Later turns: one word."""

    size: int = 60_000
    turns: int = 0

    @property
    def _llm_type(self) -> str:
        return "runaway"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "Runaway":
        return self

    def _generate(self, *args: Any, **kwargs: Any) -> ChatResult:
        raise NotImplementedError

    async def _astream(
        self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any
    ):
        self.turns += 1
        if self.turns > 1:
            chunk = ChatGenerationChunk(message=AIMessageChunk(content="done"))
            if run_manager:
                await run_manager.on_llm_new_token("done", chunk=chunk)
            yield chunk
            return
        args = HEAD + RAMBLE * (self.size // len(RAMBLE)) + '"\n'
        first = [
            {
                "name": "task",
                "args": "",
                "id": "call_1",
                "index": 0,
                "type": "tool_call_chunk",
            }
        ]
        yield ChatGenerationChunk(
            message=AIMessageChunk(content="", tool_call_chunks=first)
        )
        for i in range(0, len(args), 6):
            piece = [
                {
                    "name": None,
                    "args": args[i : i + 6],
                    "id": None,
                    "index": 0,
                    "type": "tool_call_chunk",
                }
            ]
            chunk = ChatGenerationChunk(
                message=AIMessageChunk(content="", tool_call_chunks=piece)
            )
            if run_manager:
                await run_manager.on_llm_new_token("", chunk=chunk)
            yield chunk
            if i % 600 == 0:
                await asyncio.sleep(0)  # the network: the stream yields now and then


async def main() -> None:
    size = int(next((a for a in sys.argv[1:] if a.isdigit()), "60000"))
    if "--fixed" in sys.argv:
        from gen9_agent import partial_json

        partial_json.install()
    parses: list[tuple[int, float]] = []
    parse = ai.parse_partial_json

    def timed(s: str, *, strict: bool = False) -> Any:
        start = time.perf_counter()
        try:
            return parse(s, strict=strict)
        finally:
            if len(s) > 1000:
                parses.append((len(s), time.perf_counter() - start))

    ai.parse_partial_json = timed
    longest = 0.0

    async def tick() -> None:
        nonlocal longest
        last = time.perf_counter()
        while True:
            await asyncio.sleep(0.1)
            now = time.perf_counter()
            longest = max(longest, now - last - 0.1)
            last = now

    ticking = asyncio.create_task(tick())
    agent = create_deep_agent(model=Runaway(size=size), checkpointer=InMemorySaver())
    start = time.perf_counter()
    parts = 0
    async for _ in agent.astream(
        {"messages": [{"role": "user", "content": "Fact-check a claim."}]},
        {"configurable": {"thread_id": "probe"}},
        stream_mode=["messages", "updates"],
        subgraphs=True,
        version="v2",
    ):
        parts += 1
    ticking.cancel()
    print(
        f"{size} characters of rambling, {'fixed' if '--fixed' in sys.argv else 'as released'}:"
    )
    print(f"  the turn took {time.perf_counter() - start:.1f} s, {parts} stream parts")
    print(f"  the event loop's longest stall: {longest:.1f} s")
    for length, seconds in parses:
        print(f"  parse_partial_json of {length} characters: {seconds:.2f} s")


asyncio.run(main())
