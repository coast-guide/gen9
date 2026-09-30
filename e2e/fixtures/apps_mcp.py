"""An MCP server with an app, for e2e/apps.mjs (MCP Apps, `io.modelcontextprotocol/ui` 2026-01-26):
FastMCP 4.0.9 with a UI tool (`show_board`), a tool only its View may call (`move`), and the View:
plain HTML speaking JSON-RPC to its host, no SDK. The View shows what it was sent and what it could
do, for the check to read:
- `#result`: the board from the tool's result (`structuredContent`);
- `#isolated`: whether it can reach the page that embeds it (it must not);
- `#blocked`: whether its request to an origin it didn't declare was blocked by the CSP;
- buttons that play a move (`tools/call move`), open a link (`ui/open-link`) and send a message
  (`ui/message`), each answer in `#played`, `#opened`, `#sent`.
No sign-in. `GET /test/moves` says which moves were played.

Run on the host: uv run --with fastmcp==4.0.9 python e2e/fixtures/apps_mcp.py <port>
It serves http://host.docker.internal:<port>/mcp (gen9-agent must allow it: CONNECTORS_ALLOWED_HOSTS).
"""

import sys

from fastmcp import FastMCP
from fastmcp.apps.config import UI_MIME_TYPE, AppConfig, ResourceCSP
from starlette.requests import Request
from starlette.responses import JSONResponse

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 17803
VIEW = "ui://board/view.html"
mcp = FastMCP(name="board")
MOVES: list[int] = []


@mcp.tool(app=AppConfig(resource_uri=VIEW))
def show_board(size: int) -> dict:
    """Show a board of `size` cells, which the person can play in its app."""
    return {"size": size, "cells": [0] * size}


@mcp.tool(app=AppConfig(resource_uri=VIEW, visibility=["app"]))
def move(cell: int) -> dict:
    """Play a move on the board (its app only)."""
    MOVES.append(cell)
    return {"played": cell}


@mcp.custom_route("/test/moves", methods=["GET"])
async def moves(request: Request) -> JSONResponse:
    return JSONResponse(MOVES)


@mcp.resource(
    VIEW,
    mime_type=UI_MIME_TYPE,
    # A domain it declares (never reached); the check asks for another, which must be blocked
    app=AppConfig(csp=ResourceCSP(connect_domains=["https://api.example.com"]), prefers_border=True),
)
def view() -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Board</title>
<style>body{{font:14px system-ui;margin:12px}} button{{margin-right:6px}}</style></head>
<body>
<h1 style="font-size:16px;margin:0 0 8px">Board</h1>
<p id="result">waiting for the result</p>
<p>Isolated: <span id="isolated">?</span>. Undeclared request: <span id="blocked">?</span>.</p>
<button id="play">Play cell 1</button><button id="open">Open the rules</button><button id="send">Ask about cell 1</button>
<p id="played"></p><p id="opened"></p><p id="sent"></p>
<script>
let next = 1;
const pending = new Map();
const request = (method, params) => new Promise((resolve, reject) => {{
  const id = next++;
  pending.set(id, {{ resolve, reject }});
  parent.postMessage({{ jsonrpc: "2.0", id, method, params }}, "*");
}});
const notify = (method, params) => parent.postMessage({{ jsonrpc: "2.0", method, params }}, "*");
const show = (id, text) => (document.getElementById(id).textContent = text);
addEventListener("message", (event) => {{
  const m = event.data || {{}};
  if (m.id !== undefined && pending.has(m.id) && !m.method) {{
    const p = pending.get(m.id); pending.delete(m.id);
    m.error ? p.reject(new Error(m.error.message)) : p.resolve(m.result);
    return;
  }}
  if (m.method === "ui/notifications/tool-result") {{
    const board = (m.params && m.params.structuredContent) || {{}};
    show("result", "cells: " + (board.cells || []).length);
  }}
  if (m.method === "ui/resource-teardown" && m.id !== undefined) parent.postMessage({{ jsonrpc: "2.0", id: m.id, result: {{}} }}, "*");
  if (m.method === "ping" && m.id !== undefined) parent.postMessage({{ jsonrpc: "2.0", id: m.id, result: {{}} }}, "*");
}});
try {{ void window.top.document.title; show("isolated", "no"); }} catch {{ show("isolated", "yes"); }}
document.addEventListener("securitypolicyviolation", (e) => {{ if (e.violatedDirective.startsWith("connect-src")) show("blocked", "blocked"); }});
fetch("http://127.0.0.1:{PORT}/test/moves").then(() => show("blocked", "not blocked"), () => {{}});
document.getElementById("play").onclick = () =>
  request("tools/call", {{ name: "move", arguments: {{ cell: 1 }} }}).then(
    (r) => show("played", "played " + (r.structuredContent ? r.structuredContent.played : "?")),
    (e) => show("played", "refused: " + e.message));
document.getElementById("open").onclick = () =>
  request("ui/open-link", {{ url: "https://example.com/board-rules" }}).then(() => show("opened", "opened"), (e) => show("opened", "not opened: " + e.message));
document.getElementById("send").onclick = () =>
  request("ui/message", {{ role: "user", content: [{{ type: "text", text: "Tell me about cell 1" }}] }}).then(() => show("sent", "sent"), (e) => show("sent", "not sent: " + e.message));
request("ui/initialize", {{ appInfo: {{ name: "board", version: "1.0.0" }}, appCapabilities: {{}}, protocolVersion: "2026-01-26" }}).then(() => {{
  notify("ui/notifications/initialized", {{}});
  notify("ui/notifications/size-changed", {{ width: document.body.scrollWidth, height: document.documentElement.scrollHeight }});
}});
</script>
</body></html>
"""


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=PORT, path="/mcp")
