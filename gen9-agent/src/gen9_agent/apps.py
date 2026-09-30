"""MCP Apps (`io.modelcontextprotocol/ui`, stable 2026-01-26): a connector tool that comes with an
interactive View, a `ui://` HTML resource the web app renders in a sandbox under its step.

- **Asking for them.** Connector clients advertise the extension (connectors.py), so servers
  that check offer their UI tools.
- **Which tools the model sees.** A tool's `_meta.ui.visibility` (default both) says whether the
  model, the View, or both may call it. The host must keep a View's own tools from the model,
  and must refuse a View's call to a tool without `"app"`.
- **What a step carries.** A UI tool's result is tagged with its View (`app` in the tool
  message's artifact, connectors.py). `tool.completed` and a saved chat's step carry it, with
  the arguments and the result the View is sent (`ui/notifications/tool-input`, `tool-result`).

`langchain.mcp` keeps a tool's `_meta` at `metadata.mcp.tool._meta`, and a result's
`structuredContent` in its artifact (explore/connectors/NOTES.md).
"""

import json
from typing import Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool

EXTENSION_ID = "io.modelcontextprotocol/ui"
MIME_TYPE = "text/html;profile=mcp-app"
# A step's arguments and result as sent to its View: bounded, whole or not at all (a cut JSON
# document is no use to a View)
MAX_APP_INPUT = 64_000
MAX_APP_RESULT = 256_000


def app_of(tool: BaseTool) -> dict[str, Any] | None:
    """The tool's View (`resource_uri`) and who may call it (`visibility`), or None."""
    meta = (tool.metadata or {}).get("mcp", {}).get("tool", {}).get("_meta") or {}
    found = meta.get("ui")
    ui: dict[str, Any] = found if isinstance(found, dict) else {}
    # The flat `ui/resourceUri` is deprecated, still sent by older servers
    uri = ui.get("resourceUri") or meta.get("ui/resourceUri")
    visibility = ui.get("visibility")
    if not isinstance(visibility, list) or not visibility:
        visibility = ["model", "app"]
    if not isinstance(uri, str) and visibility == ["model", "app"]:
        return None
    return {
        "resource_uri": uri
        if isinstance(uri, str) and uri.startswith("ui://")
        else None,
        "visibility": [v for v in visibility if v in ("model", "app")],
    }


def for_model(tool: BaseTool) -> bool:
    """Whether the model may see and call the tool."""
    app = app_of(tool)
    return app is None or "model" in app["visibility"]


def for_app(tool: BaseTool) -> bool:
    """Whether a View of the tool's server may call it."""
    app = app_of(tool)
    return app is None or "app" in app["visibility"]


def _bounded(value: Any, limit: int) -> Any:
    try:
        return value if len(json.dumps(value)) <= limit else None
    except (TypeError, ValueError):
        return None


def tagged(
    message: ToolMessage, connector_id: str, connector: str, uri: str, args: Any
) -> ToolMessage:
    """The tool message of a UI tool's call, its artifact naming the View and the arguments."""
    artifact = message.artifact if isinstance(message.artifact, dict) else {}
    app = {
        "connector_id": connector_id,
        "connector": connector,
        "resource_uri": uri,
        "input": _bounded(args, MAX_APP_INPUT),
    }
    return message.model_copy(update={"artifact": {**artifact, "app": app}})


def step_app(message: ToolMessage) -> dict[str, Any] | None:
    """A UI tool's step for the web app: its View, the arguments, and the result as MCP's
    `CallToolResult` (text content, `structuredContent`, `isError`). None for other tools."""
    artifact = message.artifact if isinstance(message.artifact, dict) else {}
    app = artifact.get("app")
    if not isinstance(app, dict) or not app.get("resource_uri"):
        return None
    blocks = message.content if isinstance(message.content, list) else [message.content]
    content = [
        {"type": "text", "text": b if isinstance(b, str) else b.get("text", "")}
        for b in blocks
        if isinstance(b, str) or (isinstance(b, dict) and b.get("type") == "text")
    ]
    result: dict[str, Any] = {"content": content, "isError": message.status == "error"}
    structured = _bounded(artifact.get("structured_content"), MAX_APP_RESULT)
    if structured is not None:
        result["structuredContent"] = structured
    if _bounded(result, MAX_APP_RESULT) is None:
        result = {"content": [], "isError": result["isError"]}
    return {
        "connector_id": app.get("connector_id"),
        "connector": app.get("connector"),
        "resource_uri": app["resource_uri"],
        "input": app.get("input"),
        "result": result,
    }


def view_meta(
    content_meta: dict[str, Any] | None, listing_meta: dict[str, Any] | None
) -> dict[str, Any]:
    """A View's `_meta.ui` (csp, permissions, prefersBorder): the content item's, else the
    listing's (the draft spec's order; the stable one only has the content item's)."""
    for meta in (content_meta, listing_meta):
        ui = (meta or {}).get("ui")
        if isinstance(ui, dict):
            return ui
    return {}
