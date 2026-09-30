# Components

The design system on screen. The foundations (color, type, radius, icons, themes, mobile-first rules,
the logo) are gen9-design's (`gen9-design/README.md`), and gen9-ui gets committed copies through
`make design-sync`. This page says which components each screen is built from, and how to add
the agent's own.

## Rules

- **Start from shadcn/ui** (style `base-maia` on Base UI, `gen9-ui/components.json`), copied into
  `gen9-ui/components/ui/`. Add one with the shadcn CLI, then adjust it to gen9-design's tokens. No
  second component library.
- **For agent components, borrow AI Elements' anatomy** (`research.md`). For example, a tool step is a
  header (icon, words, state) with collapsible details, and an approval is an alert with the
  request and two actions. Write them in `components/chat/` against Gen9's run events, instead
  of adopting the AI SDK's chat state.
- **Words come from `information-architecture.md`** ("Status words"). A component never shows an
  internal name.
- **Every interactive element:** 44 px on touch, `:focus-visible` in lapis, a label that screen
  readers get (`aria-label` when the text is an icon), and it works with the keyboard alone.
- **Motion:** only to show a state change (streaming, a step starting). None with
  `prefers-reduced-motion`.

## Inventory

| Area | Components | Where |
| --- | --- | --- |
| Primitives | Alert, Alert dialog, Avatar, Badge, Button (ink pill, ghost), Card, Dropdown menu, Empty, Field, Input, Input group, Item (list rows), Label, Separator, Sheet, Skeleton, Spinner, Switch, Table, Textarea, Toaster (sonner) | `components/ui/` |
| Shell | Sidebar (wordmark, New chat, Search, Scheduled, Recent with "Needs you" on a chat waiting for its person, Settings; Users, Plugins and Audit log for admins; account), mobile top bar and sheet, nav link, account menu (Settings; Users, Plugins and Audit log for admins; Appearance, Sign out) | `components/app-shell/` |
| Chat | Conversation, message (the person's in a bubble on the right; Gen9's as prose after the mark), composer (pinned bottom, send and Stop), activity (plan with ticks, steps as they happen, folded after), Markdown answers, suggestions on the empty chat, the line under the composer ("Gen9 is an AI system and can be wrong. Open the sources…", there before the first question), the question card (a form titled "Gen9 needs your answer": each question as a fieldset, choices as native radios styled as pills with "Other" last, a text field otherwise, Send answer), the approval card (a section titled "Gen9 wants to …": what it would add and remove, or every argument in full, with text that hides characters marked and warned of, Deny (ghost, then an optional "What should Gen9 do instead?") and Allow (ink pill)), the permission mode menu in the composer (a ghost button labelled with the mode, radio items with a line each), a declined step (a cross, "You declined: …" and the reason), the Retry card ("Gen9 couldn't finish", the reason, Retry), Sources (a button under an answer opening a sheet: "Cited", then "Also consulted"; a link Gen9 didn't find among its pages says so), a chat's files (attach, list, download), a connector's app (its View in a sandboxed frame of its own origin; what it asks the person to confirm in a bar under it, whose Allow counts only after half a second of stillness, and whose text replaces a draft only on "Replace mine"), a connector's question (elicitation: a form, or a link to open), background tasks (each opening its own chat), a checked answer's verdict line | `components/chat/` |
| Settings | Section with titled rows: label, value, one action on the right; a choice among a few as native radios in a fieldset ("Email me" as a list, Theme as segments); a plugin row (title, version, its marketplace, what it brings, Add or Remove, or "Everyone has it"); the skill list (name, "Gen9's own" or "From <plugin>", description); Memory (the Markdown, "Updated …", Edit in place with a character count, Clear behind a confirm dialog); a control row with a switch (remember, search past chats); a connector row (its tools, and tools its server changed waiting for a look); an environment secret row (host, "Sent for reading only" or "reading and changing"); where you're signed in, with Sign out per browser; your data (Download, the privacy page); delete account | `components/settings/` |
| Admin | User row: avatar, name, badges ("You", "Admin"), email and join date, a menu of actions; plugin source row (name, URL, "Synced … · N plugins" or "Syncing…" or "Couldn't sync: …" over "Last synced … · N plugins", a menu: Sync now, Remove…), the add-a-source form, plugin row (title, version, a format badge, what it brings in one line, a native select for who may have it, Details, a plugin that changed since it was made available waiting with what changed, each skill's files to read); an admin page for someone who isn't one ("You need admin access") | `components/admin/` |
| Scheduled | Task row (name, "Paused" or "Done", its schedule in words with the time zone and mode, "Next: in 3 hours", its latest runs as links with their state, skipped ones and why, a menu: Run now, Pause or Resume, Edit, Delete…); the task form (Name, What Gen9 should do, When, a time, date or day, While it runs); the API trigger dialog (its address, a token shown once, made again or revoked) | `components/scheduled/`, `app/(app)/scheduled/` |
| Search | Search field (input group: icon, input, Clear), mode switch (native radios styled as pills: All, Words, Meaning, Title), result rows in a card (title, snippet, relative date), each outcome a section named for its query and mode | `components/search/`, `app/(app)/search/` |
| Brand | Mark and wordmark | `components/brand/` (from `gen9-design/react/`) |

## To add, as their screens are built

| Component | Anatomy | For |
| --- | --- | --- |
| Chat status | A small text label on a Recent row: "Working", "Didn't finish". Nothing when done. "Needs you" is built (with questions) | Sidebar |
| Citation | A numbered link in the answer, opening the source (or the past chat) | Memory, search as a tool |
