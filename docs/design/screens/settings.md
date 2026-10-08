# Settings

A person's account, sign-in and preferences. Principles: 2 (in charge), 5 (memory you can see, when
it comes), 9 (private).

## Today (built)

Titled sections of rows (label, value, one action on the right), in one column:
- **Profile:** name (Edit), email with "Verified", role ("Member" or "Admin"), and "Model use":
  how much of the usage limit is used and when it resets ("Less than 1% of your limit, which
  resets on 1 October 2026", "All of your limit: it resets on …", "No limit"; P5-D1). Left out
  when the router can't say.
- **Sign-in and security** ("Changes open Gen9's secure sign-in page, then bring you back"): password
  with its date (Change password), authenticator app (Set up, or remove), passkeys (Add a passkey,
  each listed), recovery codes. Back from Keycloak, a toast names what was done: "Password
  changed.", "Profile saved.", "Passkey added.", "Passkey removed.", "Authenticator app set up.
  Recovery codes saved." (a first app goes on to its codes), "Authenticator app removed. Its
  recovery codes went with it." (the last app). A cancelled action says nothing.
- **Where you're signed in:** each session, "This browser" marked; sign out others, or everywhere.
- **Apps with access** ("Apps you let use your account: Gen9's terminal, and agents that use Gen9
  for you"): each app a person allowed on a consent screen (Gen9 CLI, "Agents (MCP and A2A)", a
  client registered by its metadata document), what it may do in that screen's words ("Can see
  your name; …"), and when it was allowed. *Remove access* asks first ("Remove access for …? It's
  signed out of your account, and asks you again if it wants to come back."). With none: "When you
  sign in to Gen9's terminal or let an agent use Gen9, it appears here."
- **Appearance:** system, light or dark (also in the account menu): native radios drawn as segments, a group named "Theme" as its row is, one Tab stop, the arrow keys choosing.
- **Your data:** "Download a copy": a ZIP at once, of JSON for machines and a README for people:
  the account, every chat as Gen9 shows it, memory, scheduled tasks, connectors and environment
  secrets' settings, plugins, and the chats' files; no tokens or secret values (GDPR Art. 20, as
  ChatGPT and Claude offer it, without their emailed link).
- **About Gen9:** "Version": Gen9's version and the commit its images were built from ("0.1.0,
  commit 0123abc", or "built from local code"), from gen9-agent's `GET /v1/version`; left out when
  the API can't say. Above Delete account, which stays last.
- **Delete account:** needs a sign-in from the last 5 minutes, then deletes everywhere.

## Memory

Principles: 5 (memory you can see and change), 2 (in charge), 3 (honest). The agent keeps one
Markdown file per person (gen9-agent's `memory.py`); this is where the person reads and corrects it.

```
Memory
What Gen9 remembers about you. Every chat reads it as it is now, new chats and ones you've already started.
┌──────────────────────────────────────────────────────────────┐
│ - The user's favourite colour is teal.                       │  the memory, as Markdown
│ - Prefers metric units.                                      │
│ Updated 5 minutes ago                          Edit   Clear  │
└──────────────────────────────────────────────────────────────┘
```

- **Placement:** after Profile, since it's about the person, before sign-in and security.
- **Reading:** rendered like a chat answer (the chat's Markdown component), with "Updated …" and
  the relative date.
- **Empty:** "Nothing yet. Tell Gen9 to remember something in a chat, or add it here.", with
  "Add".
- **Edit** (and Add) turns the card into a text field with the Markdown, labelled "What Gen9
  remembers":
  - it shows "N of 16,000 characters" and won't take more;
  - "Save" (ink pill) and "Cancel";
  - a toast "Memory saved." on success, and the error in plain words otherwise.
- **Clear** asks first, in a dialog: "Clear everything Gen9 remembers about you?", with "Gen9
  won't know any of it, in new chats or ones you've already started. You can't undo this.", then
  "Cancel" and "Clear memory" (destructive).
- **Every chat reads it anew** at each message: an edit, a Clear or memory turned off reach a chat
  already begun (gen9-agent's `FreshMemory`; Deep Agents alone kept what a chat first read).
  After: the empty state, and a toast "Memory cleared."
- **Accessibility:** the text field has a visible label, the dialog traps focus and returns it to
  Clear, and the count is announced politely. It's checked by `e2e/a11y.mjs` (Settings) and
  `e2e/memory.mjs` in Chrome.

### Remember things about me

Under the memory card, first of two switch rows, as Claude's memory can be paused: "Remember
things about me", with "Gen9 keeps what helps future chats, never sensitive details you didn't
ask it to. Off, it neither uses nor adds to this memory." Off, the card's memory stays, to use
again when it's turned on.

### Search and reference past chats

Under the memory card, the second switch row, as Claude's "Search and reference chats": "Search and
reference past chats", with "Gen9 looks through your earlier chats when you mention one, and says
which it used." It's on by default. Off, a chat's agent isn't given the tools to search them.
The switch saves at once ("Saved.") and goes back if saving fails. Its label and hint are tied to
it (`htmlFor`, `aria-describedby`).

## Connectors (milestone 2)

Principles: 2 (in charge), 9 (private). The services Gen9 may use on the person's behalf, each a
remote MCP server. References:
- Claude's custom connectors (support.claude.com, "Get started with custom connectors using remote
  MCP"): added by name and URL, with per-tool permissions ("Needs approval", "Allow always",
  "Blocked"), and advice to allow always only what you trust to run unsupervised;
- MCP 2026-07-28: a human able to deny calls, and a tool's own claims (annotations) untrusted
  unless you trust the server.

```
Connectors
Services Gen9 can use for you. It asks before using them, unless you say otherwise.
┌──────────────────────────────────────────────────────────────┐
│ DeepWiki   mcp.deepwiki.com · 3 tools        [Ask every time ▾]  Remove │
│ Notes      notes.example.com · 2 tools       [Ask before changes ▾] Remove │
│ + Add a connector                                             │
└──────────────────────────────────────────────────────────────┘
```

- **Placement:** after Memory, before sign-in and security.
- **Each connector:** its name, its host and how many tools it offers (expandable to list them),
  its policy, and Remove, which asks first.
- **Policies:**
  - "Ask every time", the default: each call waits for Allow or Deny;
  - "Ask before changes": calls the server marks read-only run at once;
  - "Don't ask".

  A chat set to "Ask before acting" still asks for anything not marked read-only.
- **Add a connector:** a name, the server's URL (https), and optionally a token sent with each
  call. Gen9 connects before saving:
  - it shows the tools it found;
  - or it says what went wrong: can't reach it, not an MCP server, or the token was refused.

  The token is stored encrypted, and never shown again.
- **Servers that need sign-in**:
  - adding one sends the browser to the server's sign-in and back to Settings, which says
    "Signed in. Gen9 can use Notes now." (or why not, as an alert);
  - until then the row says "Sign in at notes.example.com to use it", with a Sign in button;
  - when a sign-in lapses (a refresh refused), it says "Its sign-in has lapsed. Sign in again to use
    it.", with Reconnect, and its tools leave chats until then.
- **Tools that changed** (P5-C4): a server that rewords a tool or adds one after
  the person connected it (a "rug pull") doesn't reach chats until they look. Under the name, in a
  bordered group:
  - "2 tools changed since you connected it. Gen9 won't use them until you look. If you don't
    recognise a change, remove the connector.";
  - each tool: its name and "changed" or "new", "Now: <what it says>", and "Before: <what it said>"
    for a changed one;
  - "Use them as they are now" (outline), which keeps the versions shown; the group goes.

  The token field says servers that need sign-in send you there instead. That follows Claude's
  custom connectors, where a URL alone starts the server's own sign-in.
- **Browse the directory**: next to "Add a connector".
  - A search over Gen9's copy of the MCP Registry. Each result shows its title, host and registry
    name, "needs a key" when it asks for one, its description, a link to its repository, and
    Add.
  - The panel says the servers are "as their publishers describe them. Gen9 hasn’t reviewed
    them."
  - Add opens the usual form with the name and URL filled in. It says "Not reviewed by Gen9: add
    it only if you trust who runs it", and turns the token field into the server's key, named
    by its header.

  That follows the Registry's own trust model: publishers are verified by namespace, and scanning
  and curation are left to those downstream.
- **In a chat:** a connector's steps read "Used DeepWiki: read wiki structure", and its approvals
  "Gen9 wants to use DeepWiki: read wiki structure", with the inputs.
- **Accessibility:** the policy is a native select with a label naming the connector.

## Plugins and skills (milestone 4)

Principles: 2 (in charge: a plugin joins a person's chats only when they add it, or an admin gave it
to everyone), 3 (honest: what each brings), 9 (private: one person's plugins never reach another's
chats). References: ChatGPT's plugins tab, where members install what their admins made available,
and admins can pre-install for everyone (docs/plans/harness.md, milestone 4).

```
Plugins
Know-how and services your admins made available. What you add joins your chats.
┌──────────────────────────────────────────────────────────────┐
│ Tracker 5.0.1   e2e-market · 2 skills · 1 connector       Add │
│ Notes           e2e-market · 1 skill           Everyone has it │
└──────────────────────────────────────────────────────────────┘
Skills
What Gen9 follows when a task matches.
┌──────────────────────────────────────────────────────────────┐
│ research-brief   Gen9's own · Write a research brief, …       │
│ triage-issues    From Tracker · Triage issues the team's way  │
└──────────────────────────────────────────────────────────────┘
```

- **Placement:** after Connectors, since plugins bring skills and (later) connectors.
- **Plugins:** each available plugin with its title and version, the marketplace it came from,
  what it brings in one line, and "Add" or "Remove". A plugin an admin gave to everyone says
  "Everyone has it" instead of a button. The section isn't shown when there are none. A plugin
  that changed and waits for an admin (P5-C4) says "It changed, and an admin hasn't looked at it
  yet: Gen9 doesn't use it until they do.", and its skills leave the list meanwhile.
- **Skills:** every skill the person's chats can use: Gen9's own, then their plugins', each with
  where it came from and its description. When two share a name, Gen9's own wins, then the first
  plugin by name; only the one used is listed.
- **In a chat:** a plugin's skill step reads "Used the triage issues skill from Tracker". A plugin
  added or removed applies from the next message, even in an open chat.
- **Its connectors:** a plugin's remote servers join Connectors when the person has the plugin,
  each marked "From <plugin>", with its policy but no Remove: it goes with the plugin. One that
  needs sign-in waits for it there, as any connector does.

## Notifications (milestone 5)

Principle 8: tell people once when something changes, and say whether it needs them. Principle 9:
the answer stays in Gen9.

- **Placement:** after Skills, as "Notifications", "Gen9 tells you once when a scheduled task is
  done or needs you."
- **One row:** "Email me", a group of native radios named by it: "When a task finishes or needs
  me" (the default), "Only when a task needs me", or "Never". Radios, not a select: all three
  show, and they wrap at 200% text on a phone where a select cut them off. The choice saves at
  once, with a toast; the arrow keys go through them without losing focus, and the last one is
  what's saved.
- The row explains that the email says what happened and links to the chat. Where this Gen9 has
  no SMTP server, the radios are disabled and say so.
- **The email:** "Morning brief is done" (or "needs you", or "didn't finish"), a link to the
  chat, and how to change this. Never the answer.

## Environment secrets (milestone 3)

Tokens a person's chat environments send to a service (a code host, a package registry, an API),
added to requests on their way out by OpenSandbox's credential vault: code running in the
environment never sees them. Under Connectors, as "Environment secrets".

```
Environment secrets
Tokens your chats' environments send to a service, added on the way out: code running there never sees them.
┌──────────────────────────────────────────────────────────────┐
│ github                                               Remove  │
│ https://api.github.com/* · as Bearer · reading only          │
├──────────────────────────────────────────────────────────────┤
│ Add a secret                                                 │
└──────────────────────────────────────────────────────────────┘
```

- **A row:** its name, where it goes (`https://<host><path>`), how ("as Bearer", "as
  X-Api-Key", "as Basic") and what for ("reading only", "reading and changing"). The value is
  never shown again.
- **Add:** Name; Host (without `https://`; `*.example.com` for its subdomains; a name, not an IP
  address: OpenSandbox's vault binds names only), and the note that
  the person's environments may reach it; Path (optional, `/*`); Sent as (Authorization: Bearer,
  a header of its own, or Basic user:password); Sent for ("Reading only (GET, HEAD, OPTIONS)",
  the default, or "Reading and changing (POST, PUT, PATCH, DELETE too)", with the note that
  other requests go without it, and that in a chat acting without asking a command could use it
  for anything this allows; P5-C2); Header (only for a header of its own); Value, a
  password field, with what happens to it: kept encrypted, added on the way out, never seen by
  code in the environment, though a server that echoes requests back could show it.
- **Remove** asks first: "Your chats' environments stop sending it within seconds, and it's
  deleted."
- **Running environments** get a change within seconds; nothing restarts.

## Next (planned)

- **Permissions:** the default permission mode for new chats (human in the loop).
- **Connectors, next units:** per-tool permissions (milestone 2).
