# Plugins as they're published: survey

For milestone 4 ("Plugins published for Codex and Claude Code", `docs/plans/harness.md`).
`survey.py` loads every plugin kept inside a marketplace repository with Gen9's loader
(`src/gen9_agent/plugins.py`) and tallies what loaded and why the rest didn't.

Repositories, cloned:
- `openai/plugins` at `1dc1958` (2026-09-11), Codex's format (`.agents/plugins/marketplace.json`);
- `anthropics/claude-plugins-official` at `ad30d62`, Claude Code's format
  (`.claude-plugin/marketplace.json`).

What it showed:
- **No plugin uses the Agent Plugins format yet.** 62 of OpenAI's 65 entries are kept in the
  repository, and each has `.codex-plugin/plugin.json`. Of Anthropic's 314 entries, 52 are kept in
  the repository: 39 have `.claude-plugin/plugin.json`, and 12 have no manifest (the entry is the
  manifest). The rest are elsewhere: 3 of OpenAI's, and 262 of Anthropic's (164 `url` and 98
  `git-subdir`).
- **Read in their own formats, they load.** None was rejected:
  - OpenAI's: 62 plugins, 501 skills, 26 remote MCP servers Gen9 connects to and 5 `stdio` ones it
    won't run. 56 of 62 bring a skill or a remote server. Of the other 6, five bring only apps
    (`.app.json`, ChatGPT's own), and Zotero's one skill is dropped (below).
  - Anthropic's: 52 plugins, 29 skills, 2 remote servers and 9 `stdio`. 19 of 52 bring a skill
    or a remote server; the rest are Claude Code's own parts (commands, agents, hooks, 12 LSP
    servers), which Gen9 doesn't use.
- **Skill names often differ from their folders.** Under the Agent Skills rules, 22 of OpenAI's
  skills would be dropped for that (and 1 more for a description over 1024 characters), and 1 of
  Anthropic's. Zoom's `meeting-sdk/` folder, for one, holds `build-zoom-meeting-sdk-app`.
  Codex and Claude Code load them, and Deep Agents keeps them with a warning (`_validate_skill_name`, "warn but continue loading"). So a client's own format
  keeps them, with a note, and the Agent Plugins format stays strict, as its spec and the
  conformance kit require. One skill is still dropped: `Zotero`, whose name isn't lowercase.
- **Skipped servers**: 2 of Anthropic's need `${VAR}` values from the person's computer in
  their URL or headers (a token, typically). Gen9 can't fill them in yet.
- **Notes**: 36 of OpenAI's plugins have apps that Gen9 doesn't use. 7 remote servers sign in
  with a client registered for Codex (`oauth.client_id`); Gen9 registers its own at sign-in.
  Many skills carry frontmatter from other clients (`disable-model-invocation`, `user-invocable`,
  `version`, `tools`), which is kept.

Run it:

```bash
git clone --depth 1 https://github.com/openai/plugins /tmp/oai-plugins
git clone --depth 1 https://github.com/anthropics/claude-plugins-official /tmp/cc-plugins
uv run python explore/plugins/survey.py /tmp/oai-plugins /tmp/cc-plugins
```
