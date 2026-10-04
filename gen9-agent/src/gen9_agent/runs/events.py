"""Gen9's run events: what a client sees while a run executes, derived from the agent's stream.

The worker streams the Deep Agent with LangGraph's v2 stream parts (`stream_mode=["messages",
"updates"]`, `subgraphs=True`) and turns each part into zero or more events with `EventMapper`.
Events are stored in order (`run_events`) and replayed to clients, so they carry only JSON.

| Event | Data | When |
| --- | --- | --- |
| `run.queued` | `run_id` | The run was accepted |
| `run.started` | `attempt` | A worker began (a second attempt resumes from the checkpoint) |
| `message.delta` | `id`, `text` | Answer text as the model writes it |
| `message.completed` | `id`, `text` | The model finished a message with text |
| `status` | `text` | Something the model is doing that has no tool call of its own yet (web search) |
| `tool.started` | `id`, `name`, `args`, and `plugin` when it reads a plugin's skill | The model called a tool (the provider's own web tool included: `web_search` with its `query`, `web_open` with a `url`, `web_find` with a `pattern`) |
| `tool.completed` | `id`, `name`, `status`, `output`, `sources` when it found pages (a `task` step: those its subagent's searches found, subagent_sources.py), and for a connector tool with a View its `app` | The tool returned (`status` is `success`, `error`, or `declined`: the person denied it). `app` (MCP Apps, apps.py): `connector_id`, `connector`, `resource_uri`, the arguments (`input`) and the `result` the View is sent |
| `todos.updated` | `todos` | The agent's plan changed |
| `files.shared` | `files` (`id`, `name`, `size`, `media_type`) | After a turn that used the chat's environment: what it saved in `/work/out` that is new or changed, now the chat's files (chat_files.py) |
| `input.requested` | `id`, `kind`, and for a question its `questions` | The run paused for the person (`runs/store.py`, `wait`); it waits until every request is answered |
| `input.provided` | `id`, and for a question its `answers` | The person answered (appended by the API, `runs/control.py`); the run goes on |
| `run.completed` | `status`, `error` | The run ended: `success`, `error`, `cancelled`, or `expired` (nobody answered in time) |

Only the top-level agent speaks: parts from subagents (namespaced) are left out; a subagent
shows up as its `task` tool call, which carries the pages its searches found
(subagent_sources.py).
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from .. import approvals, apps

# A run's message carries the run's id (runs/executor.py): the history knows which run answered it
USER_MESSAGE_PREFIX = "run-"
MAX_TOOL_OUTPUT = 2000
MAX_TOOL_ARGS = 2000
# Pages kept per step and citations per answer: enough to show where an answer came from
MAX_SOURCES = 30
# Pages kept for a `task` step: its subagent may search many times, and a page the answer cites
# that isn't kept would be shown as "Not among the pages Gen9 found"
MAX_DELEGATED_SOURCES = 100


@dataclass(frozen=True)
class RunEvent:
    type: str
    data: dict[str, Any]


def json_safe(value: Any, limit: int) -> Any:
    """`value` as JSON, or a truncated string of it when it is large or not serializable."""
    try:
        text = json.dumps(value)
    except (TypeError, ValueError):
        return str(value)[:limit]
    return value if len(text) <= limit else text[:limit] + "…"


# The provider's own web tool (OpenAI `web_search_call` blocks), by what each action did
_WEB_ACTIONS = {"search": "web_search", "open_page": "web_open", "find": "web_find"}


def web_actions(message: AIMessage) -> list[dict[str, Any]]:
    """The provider's own web tool in a message: searches, pages opened, text found in a page.
    Each as a step (`id`, `name`, `args`, `completed`); they are not tool calls the agent
    executes, and they arrive complete in the model's message."""
    blocks = message.content if isinstance(message.content, list) else []
    actions = []
    for block in blocks:
        if not (isinstance(block, dict) and block.get("type") == "web_search_call"):
            continue
        action = block.get("action") or {}
        kind = action.get("type", "search")
        if kind == "open_page":
            args = {"url": action.get("url") or ""}
        elif kind == "find":
            args = {
                "pattern": action.get("pattern") or "",
                "url": action.get("url") or "",
            }
        else:
            args = {
                "query": action.get("query") or ", ".join(action.get("queries") or [])
            }
        # The pages a search consulted (`include` in model_router.chat_model); none for the others
        sources = [
            {"url": s["url"]}
            for s in action.get("sources") or []
            if isinstance(s, dict) and s.get("type") == "url" and s.get("url")
        ]
        actions.append(
            {
                "id": block.get("id") or "",
                "name": _WEB_ACTIONS.get(kind, "web_search"),
                "args": args,
                "completed": block.get("status") == "completed",
                "sources": sources[:MAX_SOURCES],
            }
        )
    return actions


def citations(message: AIMessage) -> list[dict[str, Any]]:
    """The pages an answer cites: the web search's `url_citation` annotations, with titles."""
    seen: dict[str, str | None] = {}
    for block in message.content if isinstance(message.content, list) else []:
        if not (isinstance(block, dict) and block.get("type") == "text"):
            continue
        for note in block.get("annotations") or []:
            if (
                isinstance(note, dict)
                and note.get("type") == "url_citation"
                and note.get("url")
            ):
                seen.setdefault(note["url"], note.get("title"))
    return [
        {"url": url, "title": title} for url, title in list(seen.items())[:MAX_SOURCES]
    ]


# Tools whose artifact lists the pages (or past chats, past_chats.py) they consulted; `task`'s,
# the pages its subagent's searches found (subagent_sources.py)
SOURCE_TOOLS = frozenset({"web_search", "search_past_chats", "recent_chats", "task"})


def tool_sources(message: ToolMessage) -> list[dict[str, Any]]:
    """What the router's `web_search` found, the past chats a search of them found, or what a
    subagent's searches found (their artifacts), as sources."""
    if message.name not in SOURCE_TOOLS or not isinstance(message.artifact, list):
        return []
    limit = MAX_DELEGATED_SOURCES if message.name == "task" else MAX_SOURCES
    return [
        {"url": r["url"], "title": r.get("title")}
        for r in message.artifact[:limit]
        if isinstance(r, dict) and r.get("url")
    ]


def _messages(update: Any) -> list:
    """The messages a node update adds (updates may wrap the list, e.g. in `Overwrite`)."""
    if not isinstance(update, dict):
        return []
    messages = update.get("messages")
    messages = getattr(messages, "value", messages)
    return list(messages) if isinstance(messages, list | tuple) else []


def skill_read(call: Mapping[str, Any]) -> str | None:
    """The plugin skill a tool call reads the instructions of (`/plugins/<skill>/SKILL.md`)."""
    path = str((call.get("args") or {}).get("file_path", ""))
    if call.get("name") != "read_file" or not path.startswith("/plugins/"):
        return None
    parts = path.removeprefix("/plugins/").split("/")
    return parts[0] if len(parts) == 2 and parts[1] == "SKILL.md" else None


class EventMapper:
    """Turns LangGraph v2 stream parts of one run into `RunEvent`s. Keeps a little state (which
    messages already announced a web search), so use one mapper per run."""

    def __init__(self, plugin_of: Mapping[str, str] | None = None) -> None:
        self._searching: set[str] = set()
        # Which plugin each of the person's plugins' skills came from (plugin_skills.py): a read
        # of one's SKILL.md names it, so the chat says "… skill from <plugin>" for good
        self._plugin_of = dict(plugin_of or {})
        self._summarized = False

    def map(self, part: Mapping[str, Any]) -> list[RunEvent]:
        if part.get(
            "ns"
        ):  # a subagent's own stream: shown through its `task` tool call
            return []
        if part["type"] == "messages":
            chunk, metadata = part["data"]
            return self._from_chunk(chunk, metadata)
        if part["type"] == "updates":
            events: list[RunEvent] = []
            for update in (part["data"] or {}).values():
                events.extend(self._from_update(update))
            return events
        return []

    def _from_chunk(self, chunk: Any, metadata: dict[str, Any]) -> list[RunEvent]:
        if metadata.get("langgraph_node") != "model" or not isinstance(
            chunk, AIMessageChunk
        ):
            return []
        if metadata.get("lc_source") == "summarization":
            # Deep Agents summarizing earlier messages inside the model node: its words aren't
            # the answer. Said once, as `context.summarized` (explore/context/NOTES.md)
            if self._summarized:
                return []
            self._summarized = True
            return [RunEvent("context.summarized", {})]
        events: list[RunEvent] = []
        blocks = chunk.content if isinstance(chunk.content, list) else []
        message_id = chunk.id or ""
        if message_id not in self._searching and any(
            isinstance(b, dict) and "web_search" in str(b.get("type", ""))
            for b in blocks
        ):
            self._searching.add(message_id)
            events.append(RunEvent("status", {"text": "Searching the web"}))
        if chunk.text:
            events.append(
                RunEvent("message.delta", {"id": message_id, "text": chunk.text})
            )
        return events

    def _from_update(self, update: Any) -> list[RunEvent]:
        events: list[RunEvent] = []
        for message in _messages(update):
            if isinstance(message, AIMessage):
                for action in web_actions(message):
                    step = {"id": action["id"], "name": action["name"]}
                    events.append(
                        RunEvent("tool.started", {**step, "args": action["args"]})
                    )
                    events.append(
                        RunEvent(
                            "tool.completed",
                            {
                                **step,
                                "status": "success" if action["completed"] else "error",
                                "output": "",
                                **(
                                    {"sources": action["sources"]}
                                    if action["sources"]
                                    else {}
                                ),
                            },
                        )
                    )
                if message.text.strip():
                    cited = citations(message)
                    events.append(
                        RunEvent(
                            "message.completed",
                            {
                                "id": message.id or "",
                                "text": message.text,
                                **({"citations": cited} if cited else {}),
                            },
                        )
                    )
                for call in message.tool_calls:
                    plugin = self._plugin_of.get(skill_read(call) or "")
                    events.append(
                        RunEvent(
                            "tool.started",
                            {
                                "id": call.get("id") or "",
                                "name": call["name"],
                                "args": json_safe(call.get("args", {}), MAX_TOOL_ARGS),
                                **({"plugin": plugin} if plugin else {}),
                            },
                        )
                    )
            elif isinstance(message, ToolMessage):
                output = message.text
                found = tool_sources(message)
                app = apps.step_app(message)
                events.append(
                    RunEvent(
                        "tool.completed",
                        {
                            "id": message.tool_call_id,
                            "name": message.name or "",
                            "status": "declined"
                            if approvals.declined(message.status, output)
                            else "error"
                            if message.status == "error"
                            else "success",
                            "output": output[:MAX_TOOL_OUTPUT]
                            + ("…" if len(output) > MAX_TOOL_OUTPUT else ""),
                            **({"sources": found} if found else {}),
                            **({"app": app} if app else {}),
                        },
                    )
                )
        if isinstance(update, dict) and "todos" in update:
            todos = getattr(update["todos"], "value", update["todos"])
            events.append(RunEvent("todos.updated", {"todos": json_safe(todos, 8000)}))
        return events
