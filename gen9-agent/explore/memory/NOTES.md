# Per-user memory with Deep Agents: probe

For the plan's "Memory per user". `probe.py` runs deepagents 0.7.18 against a throwaway Postgres
(`compose.yaml`: `exp-memory`, port 19094, tmpfs, the gen9-postgres image) with `AsyncPostgresStore`
(langgraph-checkpoint-postgres 3.1.2). Models go through gen9-models by alias.

Setup, as the docs describe (docs.langchain.com, Deep Agents "Memory", user-scoped memory), with
one change for Gen9:
- `memory=["/memories/AGENTS.md"]` loads the file into the system prompt through
  `MemoryMiddleware`. Its prompt tells the model to treat memory as reference, not instructions,
  when to save and when not, and never to store credentials.
- A `CompositeBackend` sends `/memories/` to a `StoreBackend`, and everything else to the state.
- **The namespace comes from the run's context.** The docs use `rt.server_info.user.identity`,
  which only LangGraph Server sets. Gen9 runs its own server, so the agent gets
  `context_schema=Gen9Context`, each run passes `context=Gen9Context(user_sub=…)`, and the
  factory returns `("memories", rt.context.user_sub)`.

What it showed:
- **Round 1, no memory file yet:** told to remember a colour, the agent wrote
  `/memories/user_preferences.txt`, a name of its own. `memory=` loads only `AGENTS.md`, so a new
  chat didn't know the colour.
- **Round 2, a starter `/memories/AGENTS.md` per user** ("# What Gen9 remembers about this
  person"), and permissions that allow writes to that file and deny the rest of `/memories/**`
  (first match wins, per the docs):
  - "Remember that my favourite colour is teal": `read_file`, then `edit_file` on
    `/memories/AGENTS.md`, adding "- The user's favourite colour is teal."
  - A new chat for the same user answered "teal" from the prompt, with no tool call. Another
    user's new chat didn't know.
  - Asked to save through the general-purpose subagent (`task`), the subagent's write landed in
    the same user's `AGENTS.md`. The run context and the permissions reach subagents.
  - Asked to save into `/memories/sizes.md`: `write_file` was denied, and the agent said so.
  - Deleting the item made the next chat forget.
- **The stored item** has key `/AGENTS.md` (the path under the route) in namespace
  `("memories", <sub>)`. Its value is `{content: str, encoding: "utf-8", created_at,
  modified_at}` (ISO timestamps). An API can show `content` as it is, and `modified_at` as "last
  updated".

For the build:
- Gen9 gives each person one memory file, `/memories/AGENTS.md`. It creates the starter before a
  run when missing, and permissions confine the agent's writes under `/memories/` to that file.
- The store lives in gen9-postgres, schema `langgraph` beside the checkpoints, and the migrate job
  sets it up.
- People see, edit and clear it in Settings. Deleting the account deletes the namespace.
