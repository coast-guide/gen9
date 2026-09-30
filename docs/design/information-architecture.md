# Information architecture

What a person finds in Gen9, where, and what things are called on screen. Words a person sees are in
quotes; code names are in `code`.

## Things

| On screen | In code | What it is |
| --- | --- | --- |
| "Chat" | thread | A conversation. Its title is its first question until titles are generated |
| (a turn) | run | One question and the work to answer it: status, plan, steps, answer. Durable (Temporal) |
| "Plan" | todos | The agent's checklist for a run, ticked off as it works |
| a step ("Searched the web: …") | tool call | One action the agent took. States below |
| a source | link in the answer | Where a claim came from |
| "Search" | `GET /v1/search` | Finding past chats by words, meaning or title |
| "Scheduled" | Temporal Schedule | A task that runs on its own: once, or on a cadence, in its time zone |
| "Needs you" | a run `waiting`, its `run_inputs` (a Temporal Signal per answer) | A question or an approval waiting for the person |
| "Ask before acting", "Act, ask when unsure" | `threads.permission_mode` (`ask`, `auto`) | The chat's permission mode |
| "Memory" | `/memories/AGENTS.md` | What Gen9 remembers about the person (Settings > Memory) |
| a skill ("Used the research brief skill") | `/skills/<name>/SKILL.md` | Built-in know-how the agent follows when a task matches; read-only |
| a plugin's skill ("Used the triage skill from Tracker") | `/plugins/<name>/SKILL.md` | Know-how from a plugin the person has (Settings > Plugins); read-only |
| a helper ("Asked the fact checker: …") | a subagent (`agents/<name>/AGENTS.md`) | An agent Gen9 delegates part of a task to; the general-purpose one is "a helper" |
| "Connectors" | MCP servers | Other services Gen9 may use on the person's behalf (Settings > Connectors) |
| "Environment" | a sandbox (OpenSandbox) | The chat's own machine, where Gen9 runs commands and keeps files |
| "Environment secrets" | `environment_secrets` | Values the environment sends for the person to the hosts they name; the agent never sees them (Settings) |
| a background task | a chat of its own (`threads.parent_id`) | Work the agent starts that goes on while it keeps talking |
| "Model use" | the router's budget for the person | How much of their model usage limit they've used, and when it resets (Settings > Profile) |
| "Settings" | Keycloak account and gen9-ui | Profile, sign-in and security, sessions, appearance, account |
| "Users" (admins) | Keycloak Admin API | Who can sign in |
| "Plugins" (admins), a "source" | `plugin_sources`, `plugins` (gen9-agent) | Git repositories plugins come from, and who may have each: "Nobody", "People who add it", "Everyone" |

## Where things are

Desktop: a sidebar and the page. Phone: a top bar (menu, the mark, new chat) and the same sidebar
in a sheet.

```
Sidebar
  gen9                          wordmark, home
  New chat                      /chat
  Search                        /search
  Scheduled                     /scheduled
  Recent                        chats, newest first; "Needs you" on one waiting for the person
  ─────
  Settings                      /settings
  Users                         /admin/users           (admins only)
  Plugins                       /admin/plugins         (admins only)
  Audit log                     /admin/audit           (admins only)
  Name, email                   account menu: Settings, Users, Plugins and Audit log (admins), Appearance, Sign out
```

| Route | Screen | State |
| --- | --- | --- |
| `/` | Home (signed out): what Gen9 is in one screen, and the two ways in; redirects to `/chat` when signed in | Built (`screens/home.md`) |
| `/chat`, `/chat/<id>` | Chat: empty with suggestions, or a conversation | Built (`screens/chat.md`) |
| `/search` | Search past chats | Built (`screens/search.md`) |
| `/scheduled` | Scheduled tasks: new, edit, run now, pause, resume, delete; each with its schedule, next run and latest runs | Built (`screens/scheduled.md`) |
| `/settings` | Profile (with model use), memory, connectors, plugins, skills, notifications, environment secrets, sign-in and security, where you're signed in, apps with access, appearance, your data (download, privacy), delete account | Built (`screens/settings.md`) |
| `/privacy` | What Gen9 keeps, who else receives it, for how long, and a person's rights (GDPR Art. 13); readable before signing up, linked from Settings and every Keycloak page; or the operator's own notice (`PRIVACY_NOTICE_URL`) | Built |
| `/admin/users` | Users: list, search, enable and disable, admin role, sign out, reset password, unlock, delete | Built (`screens/admin-users.md`) |
| `/admin/plugins` | Plugins: sources (add, sync now, remove), every plugin with what it brings and who may have it | Built (`screens/admin-plugins.md`) |
| `/admin/audit` | Audit log: who did what, newest first; everything, or only access Gen9 refused | Built (`screens/admin-audit.md`) |
| `/signed-out`, `/auth/error` | After sign-out; sign-in errors, including “Sign-in is unavailable right now.” when Keycloak doesn't answer (checked before sending the browser there) | Built |
| (any address Gen9 doesn't have) | “There's nothing here.” (`app/not-found.tsx`) | Built |
| (a page that failed on the server) | “Gen9 couldn't load this page.”, Try again (`app/error.tsx`) | Built |
| (the root layout failed) | The same words in a document of its own, styled without the app's stylesheet (`app/global-error.tsx`) | Built |
| Keycloak pages | Sign in, sign up, reset password, two-factor, passkeys | Built (gen9-keycloak's theme) |

## Status words

A run's state as a person sees it. The code's names never reach the screen.

| Run state (`runs.status`, events) | On screen, while it happens | After |
| --- | --- | --- |
| `queued` | "Thinking" | — |
| `running`, working | The plan and each step, live ("Searched the web: postgres 18"), under a label saying what's happening ("Thinking", "Searching the web") | Folded into one line, "Used 3 tools and a plan" |
| `success` | — | The answer |
| `cancelled` | — | The page stops answering, and the run ends on the server within about 2 s. An answer stopped before any text says "This answer didn't finish." under its steps |
| `error` | — | A message saying what to do: "Gen9 couldn't answer. Try again." |
| `waiting` for Retry | "Gen9 couldn't finish", with why (the provider didn't answer, or the usage limit and when it resets) and Retry | Retry continues where it was |
| `waiting` | "Needs you": the question card, or the approval card with Allow and Deny; the composer waits; the Recent row says "Needs you" | "Asked you: …" and "You answered: …", or the action's own step, or "You declined: …" with the reason |
| `expired` | — | "Gen9 stopped waiting for an answer." (after 7 days without one); the next message continues the chat |

A step's state follows the AI SDK's tool states (`research.md`): working (`input-available`), needs
approval (`approval-requested`), done (`output-available`), failed (`output-error`), declined
(`output-denied`; gen9-agent's `declined`).
