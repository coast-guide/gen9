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
- **An answer's length.** The router caps each call's output (gen9-models/config.yaml,
  `max_tokens`). An answer cut off there is useless, or worse: a tool call cut off in its arguments
  still parses, with them truncated, and would run (half a file written, a command cut short).
  `OutputLimit` keeps such an answer's text, drops its tool calls and says it was cut off. A
  model once wrote 114,559 tokens of one tool call's arguments in 13 minutes (manual-e2e.md, P6-Z1).

Deep Agents' subagents don't inherit the main agent's middleware, so the main agent and each
subagent get their own (agent.py).
"""

from datetime import UTC, datetime
from typing import Any

from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallLimitMiddleware,
    ModelResponse,
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
CUT_OFF = (
    "I stopped here: this answer reached the longest one answer may be, and was cut off, so I "
    "didn't do what it was about to do. Ask for it in smaller parts."
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


def cut_off(message: AIMessage) -> bool:
    """Whether the model stopped at its output limit: Chat Completions' `finish_reason`, or the
    Responses API's `incomplete` status."""
    meta = message.response_metadata
    if meta.get("finish_reason") == "length":
        return True
    reason = (meta.get("incomplete_details") or {}).get("reason")
    return meta.get("status") == "incomplete" and reason == "max_output_tokens"


class OutputLimit(AgentMiddleware):
    """An answer cut off at the router's output limit: its text kept with `CUT_OFF` after it, its
    tool calls dropped, so the turn ends there."""

    async def awrap_model_call(self, request, handler):
        response = await handler(request)
        result = []
        for message in response.result:
            if isinstance(message, AIMessage) and cut_off(message):
                text = message.text.strip()
                message = message.model_copy(
                    update={
                        "content": f"{text}\n\n{CUT_OFF}" if text else CUT_OFF,
                        "tool_calls": [],
                        "invalid_tool_calls": [],
                    }
                )
            result.append(message)
        return ModelResponse(
            result=result, structured_response=response.structured_response
        )


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
    """The grounding for one agent: today's date, the search budget, its step budget, the
    turn's, shared with the other agents of the turn, and what an answer cut off at its length
    limit may do."""
    return [TodaysDate(), search_budget(), StepBudget(), TurnBudget(), OutputLimit()]
