# A subagent's searches in the worker's stream

`probe.py` runs real deepagents (0.7.19) with a scripted model and no network, streamed as the
worker streams (`stream_mode=["messages", "updates"]`, `subgraphs=True`, v2 parts), and prints
each ToolMessage with its namespace (docs/plans/manual-e2e.md, P8-F2).

What it showed (2026-10-04):

- A subagent's `web_search` ToolMessage arrives in an `updates` part with `ns`
  `('tools:<checkpoint task id>',)` and node `tools`, artifact (the pages) included, before the
  `task` tool's own ToolMessage, which arrives at the top level (`ns` `()`).
- Two `task` calls in one model message run in the same `tools` task: both subagents' parts carry
  the same `ns`, and nothing in them names the `task` call (deepagents gives the subagent no
  `tool_call_id`). Each `task` ToolMessage is streamed as soon as that subagent ends, so one can
  arrive while the other is still searching.

So the stream can't say which subagent found which pages when several run at once, and it is
not kept: a reloaded chat is built from the checkpoint, where `task`'s ToolMessage has no
artifact and the subagent's own messages aren't (api/threads.py, `conversation`). Gen9 therefore
collects them where the call runs: `SubagentSources` (subagent_sources.py) sets a ContextVar
collector around each `task` call, which the subagent's tools fill (the context is copied into
the subagent's tasks, the collector shared), and puts the pages on `task`'s ToolMessage as its
artifact. `tests/test_subagent_sources.py` runs two subagents at once, each with its own pages.
