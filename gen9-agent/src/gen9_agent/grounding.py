"""What every model call is told about the moment it runs in, and how far a turn may search.

- **Today's date.** A model knows only its training data's time. Without the date, a model asked
  for "the current release" may take its own cutoff for today, or invent a later date, and search
  on for a version that doesn't exist: DeepSeek-V4.1-Flash made 50 searches for "PostgreSQL 18.7,
  November 2026" in one turn (docs/plans/harness.md, Surprises). `TodaysDate` adds
  the date to the system prompt of each model call.
- **A search budget.** LangChain's `ToolCallLimitMiddleware` refuses `web_search` after
  `SEARCHES_PER_TURN` calls in a turn, telling the model not to call it again, so it answers with
  what it found rather than searching without end.
- **A step budget.** LangChain's `ModelCallLimitMiddleware` ends a turn after
  `MODEL_CALLS_PER_TURN` model calls, whatever the model loops on, with a plain answer saying so.
  Deep Agents allows 9,999 steps, and a model stuck calling tools would spend without end
  (docs/plans/harness.md, "Spend can't run away").

Deep Agents' subagents don't inherit the main agent's middleware, so the main agent and each
subagent get their own (agent.py).
"""

from datetime import UTC, datetime
from typing import Any

from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallLimitMiddleware,
    ToolCallLimitMiddleware,
    hook_config,
)
from langchain_core.messages import AIMessage, SystemMessage

# Enough for a researched answer with a plan (research-brief, fact-checking), not for a loop
SEARCHES_PER_TURN = 12
# About four times the heaviest turn on record (a research question that used all 12 searches);
# each agent counts its own, the main agent and every subagent
MODEL_CALLS_PER_TURN = 50
STOPPED = (
    f"I stopped here: this turn reached its limit of {MODEL_CALLS_PER_TURN} steps before I "
    "finished. Ask me to continue and I'll pick up from here."
)
# All the model calls of one turn, the agent's and its subagents' together. Each counts its own
# 50, so a turn that kept handing work to subagents could make 50 for each of them, and one such
# turn could spend the whole day's shared budget (gen9-models' GEN9_AGENT_BUDGET_USD) for
# everyone (docs/plans/manual-e2e.md, P5-C8)
MODEL_CALLS_PER_TURN_ALL = 150
STOPPED_ALL = (
    f"I stopped here: this turn reached its limit of {MODEL_CALLS_PER_TURN_ALL} steps, mine and "
    "my helpers' together, before I finished. Ask me to continue and I'll pick up from here."
)


def with_note(system: SystemMessage | None, note: str) -> SystemMessage:
    """The system message with `note` appended, whether its content is text or blocks."""
    if system is None:
        return SystemMessage(content=note)
    if isinstance(system.content, str):
        return SystemMessage(content=f"{system.content}\n\n{note}")
    return SystemMessage(content=[*system.content, {"type": "text", "text": note}])


class TodaysDate(AgentMiddleware):
    """Adds today's date (UTC) to the system prompt of each model call."""

    async def awrap_model_call(self, request, handler):
        today = datetime.now(UTC).strftime("%A, %d %B %Y")
        note = f"Today's date is {today} (UTC). Use it for anything that depends on the date."
        return await handler(
            request.override(system_message=with_note(request.system_message, note))
        )


def search_budget() -> ToolCallLimitMiddleware:
    return ToolCallLimitMiddleware(tool_name="web_search", run_limit=SEARCHES_PER_TURN)


class StepBudget(ModelCallLimitMiddleware):
    """Ends the turn after `MODEL_CALLS_PER_TURN` model calls, saying so in plain words rather
    than LangChain's "Model call limits exceeded: run limit (50/50)"."""

    def __init__(self, limit: int = MODEL_CALLS_PER_TURN) -> None:
        super().__init__(run_limit=limit, exit_behavior="end")

    async def abefore_model(self, state, runtime):  # type: ignore[override]
        stop = await super().abefore_model(state, runtime)
        if stop and stop.get("jump_to") == "end":
            return {**stop, "messages": [AIMessage(content=STOPPED)]}
        return stop


class Turn:
    """The model calls one turn has made so far, shared by its agent and subagents: the run's
    context (memory.Gen9Context.turn) reaches the subagents too."""

    def __init__(self) -> None:
        self.calls = 0


class TurnBudget(AgentMiddleware):
    """Ends the turn after `MODEL_CALLS_PER_TURN_ALL` model calls of all its agents together. A
    subagent stopped by it returns to the agent, which stops at its next call."""

    def __init__(self, limit: int = MODEL_CALLS_PER_TURN_ALL) -> None:
        super().__init__()
        self.limit = limit

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state, runtime):  # type: ignore[override]
        turn = getattr(runtime.context, "turn", None)
        if not isinstance(turn, Turn):
            return None
        if turn.calls >= self.limit:
            return {"jump_to": "end", "messages": [AIMessage(content=STOPPED_ALL)]}
        turn.calls += 1
        return None


def middleware() -> list[AgentMiddleware[Any, Any]]:
    """The grounding for one agent: today's date, the search budget, its step budget, and the
    turn's, shared with the other agents of the turn."""
    return [TodaysDate(), search_budget(), StepBudget(), TurnBudget()]
