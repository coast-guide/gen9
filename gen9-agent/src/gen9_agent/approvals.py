"""Approvals: in a chat set to "Ask before acting", a tool call that changes something waits for
the person's Allow or Deny (human in the loop, docs/plans/harness.md).

The mode belongs to the chat and reaches the agent in each run's context
(`Gen9Context.permission_mode`), so one compiled agent serves both modes: Deep Agents'
`interrupt_on` asks `when` for each call (explore/hitl/NOTES.md, "Approvals that depend on the
run"). What changes something: a write to the person's memory, deleting it included, a command
in the chat's environment (environments.py), and connector tools, by their own policy
(connectors.py). Reads never wait, nor do files the agent writes in its environment: they change
nothing outside it (a command can: it reaches the hosts the environment allows).

The interrupt is LangChain's `HITLRequest` (`action_requests`, `review_configs`); the run pauses
and waits as for a question (runs/store.py, workflows/runs.py), and resumes with
`{"decisions": [...]}`, one per action.
"""

from typing import Any, Literal

from langchain.agents.middleware import InterruptOnConfig, ToolCallRequest

from . import memory

KIND = "approval"
Mode = Literal["ask", "auto"]
DEFAULT_MODE: Mode = "auto"
MAX_REASON_CHARS = 2000
# What LangChain's HITL middleware tells the model when a call is rejected
DECLINED = "User rejected the tool call"

# Tools that change the person's memory, and the argument naming the file. `delete` once went
# unasked: in "ask" mode the agent deleted /memories/AGENTS.md with no Allow
# (gen9-learn.md, M9, F18); test_approvals.py fails if Deep Agents adds a file tool that isn't here
_MEMORY_WRITES = {
    "write_file": "file_path",
    "edit_file": "file_path",
    "delete": "file_path",
}
# A command in the chat's environment: it can change anything there, and reach the hosts it allows
_COMMANDS = {"execute"}


def asks_first(request: ToolCallRequest) -> bool:
    """Whether this call waits for Allow or Deny: in "ask" mode, when it writes the memory or
    runs a command."""
    context = request.runtime.context
    if getattr(context, "permission_mode", DEFAULT_MODE) != "ask":
        return False
    if request.tool_call["name"] in _COMMANDS:
        return True
    path = request.tool_call["args"].get(
        _MEMORY_WRITES.get(request.tool_call["name"], "")
    )
    return isinstance(path, str) and path.startswith(memory.ROUTE)


INTERRUPT_ON: dict[str, bool | InterruptOnConfig] = {
    name: InterruptOnConfig(allowed_decisions=["approve", "reject"], when=asks_first)
    for name in [*_MEMORY_WRITES, *_COMMANDS]
}


def check_decisions(
    request: dict[str, Any], decisions: list[dict[str, Any]]
) -> list[dict]:
    """The decisions for an approval request, cleaned, or ValueError saying what is wrong: one per
    action, each allowed for it, a reject's reason at most MAX_REASON_CHARS."""
    actions = list(request.get("action_requests") or [])
    configs = list(request.get("review_configs") or [])
    if not actions:
        raise ValueError("not an approval request")
    if len(decisions) != len(actions):
        raise ValueError(f"expected {len(actions)} decisions, got {len(decisions)}")
    allowed = {c.get("action_name"): c.get("allowed_decisions", []) for c in configs}
    cleaned = []
    for number, (action, decision) in enumerate(
        zip(actions, decisions, strict=True), 1
    ):
        kind = decision.get("type")
        if kind not in allowed.get(action.get("name"), []) or kind not in (
            "approve",
            "reject",
        ):
            raise ValueError(f"decision {number} must be approve or reject")
        reason = str(decision.get("message") or "").strip()
        if len(reason) > MAX_REASON_CHARS:
            raise ValueError(
                f"reason {number} is longer than {MAX_REASON_CHARS} characters"
            )
        cleaned.append(
            {
                "type": kind,
                **({"message": reason} if kind == "reject" and reason else {}),
            }
        )
    return cleaned


def declined(status: str | None, text: str) -> bool:
    """Whether a tool result says the person declined the call (a Deny)."""
    return status == "error" and text.startswith(DECLINED)
