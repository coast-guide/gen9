# Connectors: what the probe showed

`probe.py`: deepagents 0.7.18 and `langchain[mcp]` 1.4.2 (fastmcp 4.0.9, mcp 2.2.0), with a
scripted model. It uses two servers: a local FastMCP server passed in memory (`list_notes` marked
read-only, `add_note` not) and DeepWiki's public server (`https://mcp.deepwiki.com/mcp`, three
tools, no annotations, no auth).

| Question | What happened |
| --- | --- |
| Tools per run, not compiled in | A middleware adding tools in `awrap_model_call` and routing them in `awrap_tool_call` (LangChain's "runtime tool registration") works in a Deep Agent. The model saw `notes__list_notes`, `notes__add_note` and `deepwiki__…`, the calls reached the tools, and DeepWiki answered a real call. |
| Renaming | `tool.model_copy(update={"name": "<connector>__<tool>"})` keeps the MCP call on the tool's own name, which the adapter's closure captured. |
| Annotations | The adapter keeps them in `metadata["mcp"]["tool"]["annotations"]` under pydantic's field names (`read_only_hint`), not MCP's wire names (`readOnlyHint`). Read both. |
| Approvals for names known only at run time | `HumanInTheLoopMiddleware` looks names up with `interrupt_on.get(name)` and `interrupt_on[name]`. A mapping that answers for any `…__…` name, with a `when` that asks unless the tool is read-only, made `add_note` and DeepWiki's tool wait while `list_notes` ran. |
| Connections | A tool keeps its client and opens a connection per call (`async with client` in the adapter), so tools stay usable after listing. MCP 2026-07-28 has no session to keep. |
| Auth | `MCPAdapter` takes a ready `fastmcp.Client`, so a header token (or OAuth) goes on the client the adapter wraps. |

What Gen9 takes from it:
- The compiled agent is shared by everyone, so the middleware must never look tools up in a
  process-wide table. Both hooks resolve the run's connectors from its context (its person);
  a cache is keyed by connector, and a lookup by name only ever sees that person's tools.
- The policy mapping replaces `HumanInTheLoopMiddleware.interrupt_on` after construction, a
  small reliance on its internals (it reads the attribute on every call). A test pins it.

## Elicitation

`elicit_probe.py`: a FastMCP 4.0.9 tool that asks for a form and a URL, called through
`langchain.mcp` in a LangGraph graph with a checkpointer.

| Question | What happened |
| --- | --- |
| How a server asks | FastMCP's `ctx.elicit()` fails on 2026-07-28 connections ("elicitation via server-initiated requests is unavailable"). A tool asks by returning `mcp.types.InputRequiredResult(input_requests={key: ElicitRequest}, request_state=...)`, and reads `ctx.input_responses` and `ctx.request_state` when it is called again. |
| What the graph sees | `MCPAdapter` arms its clients: the result becomes a LangGraph interrupt `{"type": "mcp_elicitation", "tool_name": "plan_trip", "requests": [{key, message, mode, requested_schema \| url}]}`. `tool_name` is the server's own name, not Gen9's `<connector>__<tool>`. |
| The answer | Resuming with `Command(resume={"responses": {key: {"action": "accept", "content": {...}}, other: {"action": "decline"}}})` called the tool again, which got the content and its `request_state` back. |

## MCP Apps

`apps_probe.py`: a FastMCP 4.0.9 server over streamable HTTP with a UI tool (`show_board`), an
app-only tool (`move`, `visibility: ["app"]`) and their `ui://` resource, reached through
`MCPAdapter` with and without the client advertising `io.modelcontextprotocol/ui`.

| Question | What happened |
| --- | --- |
| Advertising support | `fastmcp.Client(transport, extensions=[mcp.client.extension.advertise("io.modelcontextprotocol/ui", {"mimeTypes": ["text/html;profile=mcp-app"]})])`: the server read it from the client's capabilities (`client_says_ui: true`), and not without it. |
| The tool's `_meta.ui` | Kept on the LangChain tool at `metadata["mcp"]["tool"]["_meta"]["ui"]` (`resourceUri`, `visibility`). The app-only tool is listed like any other, so the host must hide it from the model. |
| The result | A call returns content and an artifact `{"structured_content": ...}`. The result's own `_meta` isn't kept (FastMCP put only `io.modelcontextprotocol/serverInfo` there). |
| The View | `client.read_resource_mcp("ui://…")` returns the HTML, `text/html;profile=mcp-app`, with `_meta.ui` (`csp`, `prefersBorder`) on the content item; `resources/list` has it too. |

