"""A person's memory: one Markdown file, `/memories/AGENTS.md`, that Deep Agents loads into every
prompt (`MemoryMiddleware`) and the agent edits as it learns (explore/memory/NOTES.md).

- It lives in the LangGraph store in gen9-postgres (schema `langgraph`), under the namespace
  `("memories", <sub>)`. The namespace comes from the run's context, `Gen9Context`: Gen9 runs its
  own server, so LangGraph Server's `server_info` isn't there.
- Without a file, the agent invents one with a name of its own that nothing loads. So a starter is
  created before each run when missing, and the agent may write under `/memories/` only to that
  file.
- The person sees, edits and clears it (`api/me.py`), and deleting the account erases it
  (`accounts.py`).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from deepagents import FilesystemPermission
from deepagents.backends import StoreBackend
from deepagents.backends.utils import create_file_data
from deepagents.middleware.memory import MemoryState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.store.base import BaseStore

from .grounding import Turn, with_note

MEMORY_PATH = "/memories/AGENTS.md"
ROUTE = "/memories/"
_KEY = "/AGENTS.md"  # the path under the route, as the store keys it
# The file's first line: it tells the agent what the file is. People never see it (`read_memory`)
STARTER = "# What Gen9 remembers about this person\n"
# The whole file is in every prompt: an edit past this is refused
MAX_MEMORY_CHARS = 16_000


@dataclass(frozen=True)
class Gen9Context:
    """What a run tells the agent's graph about itself (`context=` on each run)."""

    user_sub: str
    # The chat's permission mode: "ask" makes actions wait for Allow or Deny (approvals.py)
    permission_mode: str = "auto"
    # The person's plugins' skills, by path under /plugins/ (/<skill>/SKILL.md; plugin_skills.py)
    plugin_files: Mapping[str, Any] = field(default_factory=dict)
    # A background task's run (background.py): the chat that started it, whose environment it
    # works in
    environment_of: str | None = None
    # The person lets the agent search their past chats (past_chats.py)
    search_past_chats: bool = True
    # The person lets Gen9 remember things about them: off, the run reads an empty "memory is off"
    # file instead of theirs, and writes under /memories/ are refused (MemoryRules)
    remember: bool = True
    # The model calls this turn has made, its agent's and subagents' together (grounding.py)
    turn: Turn = field(default_factory=Turn)


@dataclass(frozen=True)
class Memory:
    content: str  # without the starter line
    updated_at: datetime | None


def namespace(user_sub: str) -> tuple[str, str]:
    return ("memories", user_sub)


def paused_namespace(user_sub: str) -> tuple[str, str]:
    """Where a run reads memory while the person has it off: one file saying so."""
    return ("memories-off", user_sub)


def store_backend() -> StoreBackend:
    """Files under ROUTE, in the store, per person (the run's context names the person); while
    they have memory off, the file saying so."""
    return StoreBackend(
        namespace=lambda rt: (
            namespace(rt.context.user_sub)
            if getattr(rt.context, "remember", True)
            else paused_namespace(rt.context.user_sub)
        )
    )


# First match wins (Deep Agents' permissions): the memory file may change, nothing else under
# /memories/ may be written. Reaches subagents too (probed)
PERMISSIONS = [
    FilesystemPermission(operations=["write"], paths=[MEMORY_PATH], mode="allow"),
    FilesystemPermission(operations=["write"], paths=[f"{ROUTE}**"], mode="deny"),
]


# What a run loads instead of the person's memory while they have it off
PAUSED = (
    "# Memory is off\n"
    "The person turned off memory in Settings. Nothing about them is loaded here, and nothing "
    "you'd save is kept.\n"
)


async def ensure_memory(store: BaseStore, user_sub: str, remember: bool = True) -> None:
    """The starter file, when the person has none yet, so the agent edits it; while they have
    memory off, the file saying so."""
    if not remember:
        await store.aput(
            paused_namespace(user_sub), _KEY, dict(create_file_data(PAUSED))
        )
    elif await store.aget(namespace(user_sub), _KEY) is None:
        await store.aput(namespace(user_sub), _KEY, dict(create_file_data(STARTER)))


def _without_starter(content: str) -> str:
    return content.removeprefix(STARTER.rstrip("\n")).lstrip("\n")


async def read_memory(store: BaseStore, user_sub: str) -> Memory:
    item = await store.aget(namespace(user_sub), _KEY)
    if item is None:
        return Memory(content="", updated_at=None)
    content = _without_starter(str(item.value.get("content", "")))
    modified = item.value.get("modified_at")
    updated = datetime.fromisoformat(modified) if content and modified else None
    return Memory(content=content, updated_at=updated)


async def write_memory(store: BaseStore, user_sub: str, content: str) -> Memory:
    """The person's own edit: their text, after the starter line."""
    body = content.strip()
    if len(body) > MAX_MEMORY_CHARS:
        raise ValueError(f"memory is limited to {MAX_MEMORY_CHARS} characters")
    item = await store.aget(namespace(user_sub), _KEY)
    data = create_file_data(
        STARTER + ("\n" + body + "\n" if body else ""),
        created_at=item.value.get("created_at") if item else None,
    )
    await store.aput(namespace(user_sub), _KEY, dict(data))
    return Memory(content=body, updated_at=datetime.now(UTC) if body else None)


async def erase_memory(store: BaseStore, user_sub: str) -> int:
    """Everything under the person's namespaces: their file and anything an older version wrote,
    and the file saying memory is off, which is keyed by them too (a deleted account left it
    behind: docs/plans/manual-e2e.md, I14). Returns how many items went."""
    items = [
        item
        for space in (namespace(user_sub), paused_namespace(user_sub))
        for item in await store.asearch(space, limit=1000)
    ]
    for item in items:
        await store.adelete(item.namespace, item.key)
    return len(items)


RULES = """## What you remember

Keep the person's memory to what helps future chats: preferences, projects, how they like to work. \
Don't save sensitive details about them (health, race or ethnicity, religious beliefs, political \
views, sex life or sexual orientation, gender identity, government ID numbers, criminal history, \
immigration status) unless they explicitly ask you to remember that detail."""
RULES_OFF = """## Memory is off

The person turned off memory in Settings. Don't try to save anything about them. If they ask you \
to remember something, tell them memory is off and that they can turn it on in Settings."""
WRITES = ("write_file", "edit_file")


class FreshMemory(AgentMiddleware):
    """Each run reads the person's memory as it is now. Deep Agents' MemoryMiddleware loads it
    once per chat and keeps it in the chat's state ("only loads if not already present"), so an
    edit or Clear in Settings, or memory turned off, never reached a chat already begun: it went
    on answering from the old file (manual-e2e.md, P3-E4; deepagents issue #6122, open on
    0.7.19). A person's middleware runs ahead of Deep Agents' memory, so this puts the file into
    the same state key first, and MemoryMiddleware, finding it there, keeps it."""

    state_schema = MemoryState

    def __init__(self) -> None:
        super().__init__()
        self.store = (
            store_backend()
        )  # the person's file, or the one saying memory is off

    async def abefore_agent(self, state: Any, runtime: Any) -> dict[str, Any]:
        [found] = await self.store.adownload_files([_KEY])
        if found.error is not None and found.error != "file_not_found":
            raise ValueError(f"Failed to download {MEMORY_PATH}: {found.error}")
        if found.content is None:
            return {"memory_contents": {}}
        return {"memory_contents": {MEMORY_PATH: found.content.decode("utf-8")}}


class MemoryRules(AgentMiddleware):
    """What may be remembered, for each run: sensitive details only when asked; nothing while the
    person has memory off, when writes under /memories/ are refused too."""

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        remember = getattr(request.runtime.context, "remember", True)
        return await handler(
            request.override(
                system_message=with_note(
                    request.system_message, RULES if remember else RULES_OFF
                )
            )
        )

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        call = request.tool_call
        path = str((call.get("args") or {}).get("file_path", ""))
        if (
            call.get("name") in WRITES
            and path.startswith(ROUTE)
            and not getattr(request.runtime.context, "remember", True)
        ):
            return ToolMessage(
                "Memory is off: the person turned it off in Settings, so nothing was saved.",
                tool_call_id=call.get("id") or "",
                name=call.get("name"),
                status="error",
            )
        return await handler(request)
