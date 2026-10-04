"""The pages a subagent's searches found, on its `task` step (docs/plans/manual-e2e.md, P8-F2).

A subagent works out of sight: the chat shows its `task` call, not its steps (runs/events.py), and
the conversation keeps only the subagent's answer, `task`'s ToolMessage. The pages its searches
found would be lost, and an answer built on them would show no sources
(docs/design/screens/chat.md, "Sources").

`SubagentSources`, on the main agent and on each subagent (agent.py), gives each `task` call its
own collector, which the subagent's tools and the model's own web tool fill, then puts what it
holds on `task`'s ToolMessage as its artifact, as `web_search` carries its pages. The run's
events, the chat's history and its export read them there (runs/events.py, `tool_sources`). A
ContextVar ties each subagent to its own call when several run at once: their stream can't
(explore/subagent_sources/NOTES.md).
"""

import dataclasses
from collections.abc import Iterable
from contextvars import ContextVar
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command

from .runs.events import MAX_DELEGATED_SOURCES, tool_sources, web_actions

# The pages found by the subagent of the `task` call this runs under, by URL; None outside one
_found: ContextVar[dict[str, dict[str, Any]] | None] = ContextVar(
    "gen9_subagent_found", default=None
)


def _keep(sources: Iterable[dict[str, Any]]) -> None:
    found = _found.get()
    if found is not None:
        for source in sources:
            found.setdefault(source["url"], source)


def with_pages(result: Any, call_id: str, pages: list[dict[str, Any]]) -> Any:
    """`task`'s result with `pages` as its ToolMessage's artifact. Deep Agents returns a Command
    whose update holds that ToolMessage."""
    if isinstance(result, ToolMessage):
        return result.model_copy(update={"artifact": pages})
    if not (isinstance(result, Command) and isinstance(result.update, dict)):
        return result
    messages = [
        m.model_copy(update={"artifact": pages})
        if isinstance(m, ToolMessage) and m.tool_call_id == call_id
        else m
        for m in result.update.get("messages", [])
    ]
    return dataclasses.replace(result, update={**result.update, "messages": messages})


class SubagentSources(AgentMiddleware):
    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        call = request.tool_call
        if call.get("name") != "task":
            result = await handler(request)
            if isinstance(result, ToolMessage):
                _keep(tool_sources(result))
            return result
        found: dict[str, dict[str, Any]] = {}
        token = _found.set(found)
        try:
            result = await handler(request)
        finally:
            _found.reset(token)
        if not found:
            return result
        pages = list(found.values())[:MAX_DELEGATED_SOURCES]
        return with_pages(result, call.get("id") or "", pages)

    async def aafter_model(self, state: Any, runtime: Any) -> None:
        # The model's own web tool, in a subagent: the pages arrive in the model's message
        messages = state.get("messages") or []
        if messages and isinstance(messages[-1], AIMessage):
            _keep(s for action in web_actions(messages[-1]) for s in action["sources"])
