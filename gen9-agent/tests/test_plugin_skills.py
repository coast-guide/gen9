"""A person's plugins' skills in their chats (plugin_skills.py): which ones a person gets, and the
read-only route a Deep Agent lists and reads them from, per run."""

import uuid
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any

import pytest
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, StateBackend
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import Field

from gen9_agent import plugin_skills
from gen9_agent.memory import Gen9Context
from gen9_agent.runs.events import EventMapper, skill_read

pytestmark = pytest.mark.asyncio


def _plugin(name: str, *skills: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        name=name,
        title=None,
        report={
            "skills": [
                {"name": s, "description": s, "path": f"skills/{s}"} for s in skills
            ]
        },
    )


async def test_built_ins_keep_their_names_then_the_first_plugin_by_name() -> None:
    chosen = plugin_skills.skills_chosen(
        [
            _plugin("zeta", "review", "notes"),
            _plugin("alpha", "review", "research-brief"),
        ],
        frozenset({"research-brief"}),
    )
    assert [(p.name, s["name"]) for p, s in chosen] == [
        ("alpha", "review"),
        ("zeta", "notes"),
    ]


class Reads(BaseChatModel):
    """Reads the plugin skill's SKILL.md, tries to change it, then answers with what it read."""

    systems: list[str] = Field(default_factory=list)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.systems.append(str(messages[0].content))
        done = [m for m in messages if isinstance(m, ToolMessage)]
        if not done:
            call = {
                "name": "read_file",
                "args": {"file_path": "/plugins/greeting/SKILL.md"},
                "id": "r1",
                "type": "tool_call",
            }
        elif len(done) == 1:
            call = {
                "name": "edit_file",
                "args": {
                    "file_path": "/plugins/greeting/SKILL.md",
                    "old_string": "Say",
                    "new_string": "Shout",
                },
                "id": "e1",
                "type": "tool_call",
            }
        else:
            return ChatResult(
                generations=[
                    ChatGeneration(message=AIMessage(" | ".join(m.text for m in done)))
                ]
            )
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage("", tool_calls=[call]))]
        )

    def bind_tools(self, tools: Any, **kwargs: Any) -> "Reads":
        return self

    @property
    def _llm_type(self) -> str:
        return "reads"


class Kept:
    """plugin_files as the backend reads it, recording each file read."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.plugin_id = uuid.uuid4()
        self.content = {f"skills{path}": data for path, data in files.items()}
        self.index = {
            path: plugin_skills.SkillFile(self.plugin_id, f"skills{path}", len(data))
            for path, data in files.items()
        }
        self.read: list[str] = []

    async def contents(
        self, files: Sequence[plugin_skills.SkillFile]
    ) -> dict[plugin_skills.SkillFile, bytes]:
        self.read += [f.path for f in files]
        return {f: self.content[f.path] for f in files if f.path in self.content}


def _agent(model: Reads, kept: Kept) -> Any:
    return create_deep_agent(
        model=model,
        skills=[plugin_skills.ROUTE],
        backend=CompositeBackend(
            default=StateBackend(),
            routes={
                plugin_skills.ROUTE: plugin_skills.PluginSkillsBackend(kept.contents)
            },
        ),
        permissions=plugin_skills.PERMISSIONS,
        context_schema=Gen9Context,
        checkpointer=InMemorySaver(),
    )


SKILL = b"---\nname: greeting\ndescription: Greets people the plugin's way\n---\nSay tangerine-41.\n"


async def test_a_run_lists_and_reads_its_persons_skills_and_cant_change_them() -> None:
    """Only what is used is read: each SKILL.md for the catalogue, then the one the agent reads;
    never the skill's other files (P2-D1)."""
    kept = Kept(
        {"/greeting/SKILL.md": SKILL, "/greeting/reference.md": b"x" * 1_000_000}
    )
    model = Reads()
    result = await _agent(model, kept).ainvoke(
        {"messages": [{"role": "user", "content": "Hi"}], "skills_metadata": None},
        {"configurable": {"thread_id": str(uuid.uuid4())}},
        context=Gen9Context(user_sub="alan", plugin_files=kept.index),
    )
    assert (
        "greeting" in model.systems[0]
        and "/plugins/greeting/SKILL.md" in model.systems[0]
    )
    answer = result["messages"][-1].text
    assert "tangerine-41" in answer
    assert "permission denied" in answer.lower()
    assert set(kept.read) == {"skills/greeting/SKILL.md"}


async def test_listing_and_finding_read_nothing_and_a_search_reads_a_bounded_amount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ls and glob come from the index, with the kept sizes; grep reads files until GREP_BYTES,
    then says it stopped early; a missing file is not found."""
    kept = Kept(
        {
            "/greeting/SKILL.md": SKILL,
            "/greeting/a.md": b"needle one\n",
            "/greeting/b.md": b"needle two\n",
            "/greeting/c.md": b"needle three\n",
        }
    )
    backend = plugin_skills.PluginSkillsBackend(kept.contents)
    monkeypatch.setattr(backend, "_index", lambda: kept.index)

    listed = await backend.als("/greeting/")
    assert {(e["path"], e["size"]) for e in listed.entries or []} == {
        ("/greeting/SKILL.md", len(SKILL)),
        ("/greeting/a.md", 11),
        ("/greeting/b.md", 11),
        ("/greeting/c.md", 13),
    }
    assert [e["path"] for e in (await backend.als("/")).entries or []] == ["/greeting/"]
    found = await backend.aglob("*.md", "/greeting/")
    assert len(found.matches or []) == 4
    assert kept.read == []

    everything = await backend.agrep("needle", "/", "[abc].md")
    assert [m["text"] for m in everything.matches or []] == [
        "needle one",
        "needle two",
        "needle three",
    ]
    assert not everything.truncated
    assert "skills/greeting/SKILL.md" not in kept.read  # the glob left it out

    kept.read.clear()
    monkeypatch.setattr(plugin_skills, "GREP_BYTES", 25)
    bounded = await backend.agrep("needle", "/greeting/", "[abc].md")
    assert [m["text"] for m in bounded.matches or []] == ["needle one", "needle two"]
    assert bounded.truncated
    assert kept.read == ["skills/greeting/a.md", "skills/greeting/b.md"]

    missing = await backend.aread("/greeting/none.md")
    assert missing.error == "File '/greeting/none.md' not found"
    [download] = await backend.adownload_files(["/greeting/none.md"])
    assert download.error == "file_not_found"
    [download] = await backend.adownload_files(["/greeting/a.md"])
    assert download.content == b"needle one\n"


async def test_another_persons_run_has_none_of_them() -> None:
    model = Reads()
    kept = Kept({"/greeting/SKILL.md": SKILL})
    await _agent(model, kept).ainvoke(
        {"messages": [{"role": "user", "content": "Hi"}], "skills_metadata": None},
        {"configurable": {"thread_id": str(uuid.uuid4())}},
        context=Gen9Context(user_sub="ada"),
    )
    assert kept.read == []
    assert "greeting" not in model.systems[0]


async def test_a_read_of_a_plugin_skill_names_its_plugin() -> None:
    call = {
        "name": "read_file",
        "args": {"file_path": "/plugins/greeting/SKILL.md"},
        "id": "r1",
    }
    assert skill_read(call) == "greeting"
    assert (
        skill_read(
            {"name": "read_file", "args": {"file_path": "/plugins/greeting/notes.md"}}
        )
        is None
    )
    mapper = EventMapper({"greeting": "Greeter"})
    events = mapper.map(
        {
            "type": "updates",
            "ns": (),
            "data": {
                "model": {
                    "messages": [
                        AIMessage("", tool_calls=[{**call, "type": "tool_call"}])
                    ]
                }
            },
        }
    )
    started = [e for e in events if e.type == "tool.started"]
    assert started[0].data["plugin"] == "Greeter"


def _sql(expression) -> str:
    from sqlalchemy.dialects import postgresql

    return str(
        expression.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


async def test_a_plugins_fingerprint_is_its_files_and_report_as_the_migration_computes_it() -> (
    None
):
    # The migration that set `reviewed` for plugins already available used this SQL
    assert _sql(plugin_skills.fingerprint()).replace("plugins.", "") == (
        "encode(sha256(convert_to(concat(coalesce(digest, ''), CAST(report AS TEXT)), "
        "'UTF8')), 'hex')"
    )


async def test_a_person_has_a_plugin_only_as_an_admin_agreed_to_it() -> None:
    # P5-C4: runs and skills ask for the fingerprint an admin agreed to; keeping connectors doesn't
    assert "plugins.reviewed = encode(sha256(" in _sql(
        plugin_skills.has_plugin("sub-a")
    )
    assert "reviewed" not in _sql(plugin_skills.has_plugin("sub-a", reviewed=False))
