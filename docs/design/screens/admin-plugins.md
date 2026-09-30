# Plugins (admins)

Built: `gen9-ui/app/(app)/admin/plugins/`, `components/admin/plugin-sources.tsx`.

Where plugins come from, what each one would bring, and who may have it. Principles:
- 2, in charge: nothing reaches people until an admin makes it available, and removing a source
  confirms first;
- 3, honest about limits: what a plugin brings and what Gen9 skipped, each with why;
- 6, calm;
- 7, everyone.

Research (docs/plans/harness.md, milestone 4):
- ChatGPT's admin plugin page imports a marketplace from GitHub, syncs it daily or on demand, and
  sets each plugin to Available, Installed or Not available;
- Claude Code and VS Code add marketplaces from git.

## Layout

At phone width first; one column, the admin users screen's width and rhythm.

- **Header:** "Plugins" and "Plugins come from git repositories you add. Nobody gets one until you
  make it available."
- **Sources:** a section titled "Sources". A row per repository:
  - its marketplace's name, or the URL until it has synced;
  - the URL, with the branch or tag if one was given;
  - "Synced 5 minutes ago · 6 plugins", or "Syncing…" with a spinner, or "Couldn't sync: <why>"
    with "Last synced 3 days ago · 6 plugins" under it (what it still offers, and how old), or
    "Never synced"
    (destructive text, the reason in the words the API gave);
  - a menu: "Sync now", "Remove…". Remove asks "Remove <name>? Its N plugins go too, for
    everyone." (Alert dialog, destructive action.)
- **Add a source:** a form under the list: "Repository" (the URL, `https://…`), "Branch or tag"
  (optional), and "Add". Refusals show under the field as text (Field's error): "Use an https://
  address.", "… is on a private network …", "That repository is already a source.". While a
  source syncs, the page refreshes itself every few seconds until it's done.
- **Plugins:** a section titled "Plugins", grouped under each source's name. A row per plugin:
  - its title (or name), version, and a badge for its format when it isn't Agent Plugins
    ("Codex", "Claude Code");
  - one line of what it brings: "2 skills · 1 connector", and "1 server not run" when it has
    `stdio` ones;
  - on the right, availability as a native select labelled "Who can use <name>": "Nobody",
    "People who add it", "Everyone". The row saves it at once and says "Saved" politely (a
    toast);
  - a plugin that didn't load has no select, just its status in words: "Couldn't load: <why>",
    "Not supported: <why>", "Couldn't fetch: <why>".
- **Details** (a disclosure on the row, "Details"): the skills (name and description), the files
  Gen9 keeps of it ("Files Gen9 keeps: What its skills tell Gen9 to do: read them before people
  get it.", each path and size a disclosure that reads the file when opened, in a scrolling
  monospace block; "Not text: read it in its repository." or "Only its start is shown" when so;
  built, P5-C4), the MCP servers (name, and "Connects" or "Not run: runs on a
  computer"), what was skipped and why, and the notes ("Gen9 doesn't use its apps").
- **Empty:** no sources yet: "No plugin sources yet". Add one above, for example a repository with
  `.agents/plugins/marketplace.json` or `.claude-plugin/marketplace.json`.
- In the sidebar and the account menu as "Plugins", for admins only, next to "Users".

## States

| State | What shows |
| --- | --- |
| Loading | The page renders on the server; no skeleton |
| A source syncing | Its row says "Syncing…"; the page refreshes itself until it isn't |
| A sync failed | Its row says "Couldn't sync: <why>", and when it last synced with how many plugins it still offers; the plugins it had stay listed |
| Not an admin | "You need admin access", as on Users |
| A plugin changed since the admin chose who can use it (P5-C4) | Under its row, in a bordered group: "It changed since you chose who can use it (now at commit abc1234). Nobody gets it until you look at what it says now (Details, and its repository at that commit)." and "Let people have it again" (outline), which agrees to it as the page showed it; a change made since is refused: "It changed since you looked. Look at it again first." (a toast) |

## Words

"Source" for a repository and "plugin" for what it lists. For availability: "Nobody" is `off`,
"People who add it" is `available`, and "Everyone" is `installed`. A person's own plugins are in
Settings (the next unit).
