# Built-in, read-only skills with Deep Agents: probe

For the plan's "Skills from files (Agent Skills format), built-in and read-only first". `probe.py`
runs deepagents 0.7.18 with models through gen9-models by alias.

Sources:
- the Agent Skills specification (agentskills.io/specification): `SKILL.md` with frontmatter
  `name` (1–64, lowercase letters, digits and single hyphens, equal to its directory) and
  `description` (1–1024, what the skill does and when to use it), optional `license`,
  `compatibility`, `metadata` and `allowed-tools`; body under 500 lines;
- Deep Agents' "Skills" page (docs.langchain.com): `skills=[paths]` and `SkillsMiddleware`.
  Progressive disclosure: names and descriptions go into the prompt, the body is read when a task
  matches, and supporting files only when the body says to.

Setup:
- a skill directory in a temporary folder, served at `/skills/` by a `FilesystemBackend`
  (`virtual_mode=True`) routed from a `CompositeBackend`, with `skills=["/skills/"]`;
- one permission, deny writes to `/skills/**`;
- `FilesystemBackend`'s async methods run its file calls through `asyncio.to_thread` (the
  protocol's defaults), which fits Gen9's async rule.

What it showed:
- **Discovered through the composite route.** "Give me a brief on what a B-tree is" made the
  agent `read_file(/skills/research-brief/SKILL.md)`. The answer followed the skill exactly: its
  three headings and its closing line.
- **Only when the task matches.** "What is 17 times 23?" read no skill and answered 391.
- **Read-only.** Asked to edit the skill, the agent read it and tried `edit_file`. The permission
  refused it ("permission denied for write"), the agent said so, and the file on disk was
  unchanged.

For the build: built-in skills ship in gen9-agent's package (`src/gen9_agent/skills/<name>/`),
served read-only at `/skills/`. The chat names the step "Used the <name> skill".
