"""A person's memory file (memory.py): the starter the agent edits, what people see and change,
and erasing it. Against LangGraph's in-memory store; the Postgres store and the agent itself are
in explore/memory/NOTES.md and e2e/memory.mjs."""

from types import SimpleNamespace

import pytest
from langchain_core.messages import SystemMessage, ToolMessage
from langgraph.store.memory import InMemoryStore

from gen9_agent import memory
from gen9_agent.memory import (
    MAX_MEMORY_CHARS,
    MEMORY_PATH,
    PERMISSIONS,
    STARTER,
    ensure_memory,
    erase_memory,
    namespace,
    read_memory,
    write_memory,
)

pytestmark = pytest.mark.asyncio


async def test_a_starter_for_the_agent_that_people_never_see() -> None:
    store = InMemoryStore()
    assert (await read_memory(store, "sub-a")).content == ""
    await ensure_memory(store, "sub-a")
    item = await store.aget(namespace("sub-a"), "/AGENTS.md")
    assert item is not None and item.value["content"] == STARTER
    seen = await read_memory(store, "sub-a")
    assert seen.content == "" and seen.updated_at is None
    # The agent's edit keeps the starter line; people see what follows it
    await store.aput(
        namespace("sub-a"),
        "/AGENTS.md",
        {**item.value, "content": STARTER + "\n- Likes teal.\n"},
    )
    assert (await read_memory(store, "sub-a")).content == "- Likes teal.\n"
    await ensure_memory(store, "sub-a")  # an existing file is left alone
    assert (await read_memory(store, "sub-a")).content == "- Likes teal.\n"


async def test_people_edit_and_clear_their_own_memory_only() -> None:
    store = InMemoryStore()
    await ensure_memory(store, "sub-a")
    created = (await store.aget(namespace("sub-a"), "/AGENTS.md")).value["created_at"]
    written = await write_memory(store, "sub-a", "  - Prefers metric units.  \n")
    assert (
        written.content == "- Prefers metric units." and written.updated_at is not None
    )
    item = await store.aget(namespace("sub-a"), "/AGENTS.md")
    assert item.value["content"] == STARTER + "\n- Prefers metric units.\n"
    assert item.value["created_at"] == created  # an edit, not a new file
    await write_memory(store, "sub-b", "- Likes teal.")
    assert (await read_memory(store, "sub-a")).content == "- Prefers metric units.\n"
    with pytest.raises(ValueError):
        await write_memory(store, "sub-a", "x" * (MAX_MEMORY_CHARS + 1))
    # An older version's stray file goes too
    await store.aput(namespace("sub-a"), "/user_preferences.txt", {"content": "teal"})
    assert await erase_memory(store, "sub-a") == 2
    assert (await read_memory(store, "sub-a")).content == ""
    assert (await read_memory(store, "sub-b")).content == "- Likes teal.\n"


async def test_the_agent_may_write_the_memory_file_and_nothing_else_there() -> None:
    # First match wins: the file is allowed before the rest of /memories/ is denied
    [allow, deny] = PERMISSIONS
    assert (allow.paths, allow.mode, deny.paths, deny.mode) == (
        [MEMORY_PATH],
        "allow",
        ["/memories/**"],
        "deny",
    )


class _Request(SimpleNamespace):
    def override(self, **changes):
        return _Request(**{**vars(self), **changes})


async def test_the_rules_keep_sensitive_details_out_and_say_when_memory_is_off() -> (
    None
):
    seen = []

    async def handler(request):
        seen.append(request.system_message.text)
        return request

    for remember in (True, False):
        await memory.MemoryRules().awrap_model_call(
            _Request(
                runtime=SimpleNamespace(
                    context=memory.Gen9Context("s", remember=remember)
                ),
                system_message=SystemMessage("You are Gen9."),
            ),
            handler,
        )
    assert "health, race or ethnicity" in seen[0] and "explicitly ask" in seen[0]
    assert "## Memory is off" in seen[1] and "What you remember" not in seen[1]


async def test_with_memory_off_writes_to_it_are_refused_and_others_go_through() -> None:
    ran = []

    async def handler(request):
        ran.append(request.tool_call["args"]["file_path"])
        return "written"

    rules = memory.MemoryRules()

    def call(path: str, remember: bool):
        return SimpleNamespace(
            tool_call={"name": "edit_file", "id": "t1", "args": {"file_path": path}},
            runtime=SimpleNamespace(context=memory.Gen9Context("s", remember=remember)),
        )

    refused = await rules.awrap_tool_call(call(memory.MEMORY_PATH, False), handler)
    assert isinstance(refused, ToolMessage) and refused.status == "error"
    assert (
        await rules.awrap_tool_call(call("/work/out/notes.md", False), handler)
        == "written"
    )
    assert (
        await rules.awrap_tool_call(call(memory.MEMORY_PATH, True), handler)
        == "written"
    )
    assert ran == ["/work/out/notes.md", memory.MEMORY_PATH]


async def test_with_memory_off_a_run_reads_a_file_saying_so_not_theirs() -> None:
    store = InMemoryStore()
    await memory.write_memory(store, "s", "- Likes tea.")
    await memory.ensure_memory(store, "s", remember=False)
    paused = await store.aget(memory.paused_namespace("s"), "/AGENTS.md")
    assert paused is not None and "Memory is off" in "".join(paused.value["content"])
    # Their own is untouched
    assert (await memory.read_memory(store, "s")).content.strip() == "- Likes tea."
    # Erasing (Clear, or the account's deletion) takes the paused file too: it's keyed by them
    assert await memory.erase_memory(store, "s") == 2
    assert await store.aget(memory.paused_namespace("s"), "/AGENTS.md") is None
