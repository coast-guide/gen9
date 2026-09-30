# gen9-learn, from zero to everything

## Purpose

Anyone, with no knowledge of Gen9, can learn all of it from one document, `gen9-learn/index.html`:
what it is and why, every stack and service, every flow a person, an admin, an operator or
another program can start, where each piece of state lives, and how to run it. They learn it the
way the guide already teaches, by following one throwaway user and watching each step happen in
Chrome's DevTools, the stores, the logs and the code, and every output the page shows was
observed by a live run of `gen9-learn/verify`.

Asked by the owner: "deep dive is very much needed, they should fit into the flow
nicely … this should be exhaustive standalone document containing everything, so any new people
can know everything just from this document. anybody, zero to everything. nothing left out".

How to see it working: open `gen9-learn/index.html`; every component in the atlas (Reference)
links to the step that traces it; `cd gen9-learn/verify && node run.mjs` passes, and so does
`node reference.mjs`, which fails when a service, workflow, Schedule, API operation, table or
`make` command exists in the running system but not on the page.

## Progress

- [x] Research and plan: Decision Log, "How the document is built".
- [x] M0 Orientation: what Gen9 is and for whom, its goals and limits, what's outside it, how it's
  built and why, the cast, how to read the guide (core path, deeper steps, reference). Done (8e1df8c): `node page.mjs` passes; each claim checked against the code and the running
  stacks (SearXNG's engines read from its `/config`, admin actions from `user-actions.tsx`).
- [x] M1 Parts 1 and 2, deeper: what a person is told before signing up (privacy, the AI notice);
  search (its page, its four modes, the index); long chats (context budget, summaries); helpers
  (subagents); a provider failing (Retry); a person's model limit; attached files.
  - [x] b1d: the sign-up page's notice, `/privacy` without a session, the AI notice
    under the composer, Settings' row. Step `b1-6`.
  - [x] b2d: search's four modes and `chat_search`, the fact checker, the limit and
    Retry, the router stopped and Retry, a summarized chat on a 12,000-token worker. Steps
    `b2-retry`, `b2-limit`, `b2-context`, `b2-search`, `b2-helper`. Every check passed on a fresh
    user across runs 3 and 4 (run 4, as a reused user, failed only Meaning's rank: its extra
    long chat from run 3 outranked the lighthouse chat). Spend: $0.0025 a run.
  - [x] Attached files moved to b2xd (part 4): an attachment goes into the chat's environment,
    which part 4 introduces. Step `b2x-files`.
- [x] M2 Part 3, deeper: "Done when" (rubrics and grading); notification emails; background tasks.
  - [x] b2wd: a rubric graded short, revised in the same chat and met on try 2
    (Activities fire_task, grade_run, continue_task, grade_run, notify_outcome); one email, "…
    is done", its whole text a link and no answer; Settings' three choices; a background task
    in a chat of its own, told back by TellChatWorkflow. Steps `b2w-done`, `b2w-notice`,
    `b2w-background`. Two check faults (a truncated string, a case) fixed; re-verified in the
    full run.
- [x] M3 Part 4, deeper: connectors that sign in (OAuth), whose tools change, with an app (MCP
  Apps), that ask (elicitation), the directory; an environment's life, its secrets and egress, its
  disk; plugins that change and are reviewed.
  - [x] b2xd: every check passed across two runs as a reused user: an attached file
    (`upload`, in `/work/in`), EnvironmentWorkflow running with its `acquire` Updates, a secret
    for httpbin.org carried on GET only and never inside, OAuth sign-in with sealed tokens and
    revocation, changed tools withheld with no call to the server, the directory (22,021
    servers), a View on its own origin, elicitation, a changed plugin withheld until Ada agrees.
    Steps `b2x-oauth`, `b2x-changed`, `b2x-directory`, `b2x-app`, `b2x-asks`, `b2x-files`,
    `b2x-life`, `b2x-secret`, `b2x-review`. The disk limit is described and pointed to
    `e2e/environments.mjs`, which fills a sandbox past it: a 10 GiB fill in every gen9-learn run
    would cost minutes and disk for nothing new.
- [x] M4 Parts 5 to 8, deeper: admin roles and the audit log page; agents that register
  themselves (Client ID Metadata Documents); what an agent reaches (A2A per client); MCP's tools.
- [x] M5 Part 9, deeper: a person's data export and the privacy page's rights; what outlives an
  account and for how long.
- [x] M6 Part 10, deeper: Temporal inside (payload codec, token authorizer, internode TLS, the UI's
  admin-only sign-in); the router inside (aliases, fallbacks, SearXNG, the keys job, budgets and
  their reset, per-minute limits, the admin API, the local profile); Postgres inside (extensions,
  roles); Langfuse inside (web, worker, Redis, ClickHouse, MinIO and its lifecycle, media); the
  sandbox server inside (launch.py, execd, egress, the SNI binding, the disk watchdog); when
  things fail (limits, NUL, the database refusing writes, headers, restarts); the operator's
  toolbox (doctor, updates, audit, stop and resume agents, resealing, key rotation, logs, disk,
  upgrades, CI).
  - [x] b7d (all checks passed on its second run): payloads `binary/encrypted` in a
    history, the codec endpoint 401 and the public frontend "Request unauthorized." without a
    token, Temporal's UI turning Alan away in Gen9's words; the worker's key sees every alias and
    the API's `embed` and `rerank`; the App DB's extensions and what `gen9_agent_app` may do on
    `audit_events`; Langfuse's health and ClickHouse's tables; the sandbox server's settings;
    NUL 422 and a 20 MB body 413 before any token check; `make doctor`, `gen9-agent-reseal
    --check`, `make stop-agents` and `resume-agents` in the audit record, bounded logs. Steps
    `b7-temporal`, `b7-router`, `b7-postgres`, `b7-langfuse`, `b7-sandbox`, `b7-failures`,
    `b7-toolbox`. `make audit` and `make updates` are described, not run: they reach the
    network's advisories and Renovate, take minutes, and a new advisory would fail the guide
    for a reason that isn't the guide's.
- [x] M4 and M5 batches (b4d, b5xd, b6d) first ran in full run 1: b5xd and b6d
  passed; b4d showed the right behaviour, and three of its checks were wrong (every person is
  also in `users`; the newest audit row was the person's own refused visit). Steps `b4-admin`,
  `b5x-tools`, `b5x-reach`, `b5x-self`, `b6-export`.
- [x] Full run 1: 237 checks passed, 5 failed, all check faults, each fixed
  (b2d's one-word query, b2xd's truncated answer, b4d's group and audit-page reads).
- [x] Full run 2 passed b1 to b2xd's apps, then stopped at the elicitation step (the model asked
  its own question first); fixed (9e6eb00).
- [x] Full run 3: 245 checks passed, 1 failed: b5xd expected the first chat first among
  `search_chats`' hits, where by then part 3's brief and the background task mention
  lighthouses too; it now looks for it among them. b2d, b2xd and b4d's fixes all passed.
- [x] Full run 4 stopped in b2xd right after the OAuth sign-in: the check waited for network
  idle after choosing the connector's policy, which never came within 30 s. It now waits for the
  policy saved in Postgres.
- [x] Full run 5: every batch, 246 checks, all passed; then `reference.mjs` (17
  kinds) and `page.mjs` passed. Model spend: $0.015 on the worker's key over the whole run.
- [x] M7 Reference: the component atlas (every service), workflows, Activities and Schedules, the
  API (every operation, MCP tools, A2A methods, AG-UI, the web app's routes), the data map (every
  table and store, retention, erasure), security controls, settings (key names), operator
  commands, decisions and known risks, the glossary. `verify/reference.mjs` checks the page
  against the running system. Done, before M1 (Decision Log): `node reference.mjs`
  passes, 16 kinds: 40 services, 15 volumes, 12 workflows, 34 Activities, 4 Schedules, 78 API
  operations, 4 MCP tools, 28 tables, 8 networks, 23 make commands, 31 web app routes, 58
  gen9-agent settings, 111 settings-file keys, 7 terminal commands, 6 gen9-agent commands, and
  every link within the page. The "Seen in" links of things only a deeper step will show are
  empty until that step exists.
- [x] M8 A full `node run.mjs` and `node reference.mjs`, `node page.mjs`; gen9-learn's README and
  AGENTS.md, the root README and the manual-e2e plan in step; the PR description.
  - [x] The page itself in Chrome (the owner asked for it): no script errors; ticking
    a deeper step's Done moves its part's count in the rail (0/15 to 1/15) and survives a reload;
    the map lights the step's nodes and names it; the rail marks the part in view; Light and
    Dark switch the theme; screenshots of deeper steps at 1440 and 390 px, light and dark, read
    cleanly after two fixes (long command tags under Copy; wide tables squeezing names).

- [ ] M9 Independent audit (the owner: "Do independent verification from chrome as
  real user and see if there are any gaps and fix those, need best quality with zero miss, so
  audit the full codebase as well. Do these as independent agents … consecutively"). In order,
  one at a time; every finding checked against the live system before it's fixed, each fix its
  own verified unit:
  - [ ] 1. Me, in real Chrome (Claude in Chrome), as a reader: the page from the top, its
    commands pasted as a reader would, the app's pages a person reaches. Claude in Chrome can't
    open file:// pages, and mustn't type passwords: the page is served on 127.0.0.1:17990 from a
    folder holding only gen9-learn and gen9-design, and the app is driven through a real Chrome
    of my own (a harness taking one action at a time; the throwaway user's password is made and
    typed inside it and never leaves it). Findings:
    - F1, serious: web search results shown to a person as "Also consulted", and given to the
      model, included explicit adult sites (one a search page whose terms point at minors) and
      pages unrelated to the query (YouTube and Microsoft help, Zhihu), for a question about RFC
      10017. The scraped engines return junk at times (searxng/searxng#6671, merged 2026-09-11,
      and discussion #5651), SearXNG's safe search was off (its default, 0), and LiteLLM's
      SearXNG provider sends none. Fix: strict safe search in SearXNG (docs.searxng.org,
      `search.safe_search: 2`), and gen9-agent keeps only results sharing words with the query
      (on this chat's six searches: every junk result dropped, every relevant one kept).
    - F2, UX: Keycloak's "Choose a password" doesn't say its rules (15 to 128 characters, not
      common, not the email) before a first attempt, and its field is "Password" at first and
      "New password" after an error. Keycloak gives login-update-password.ftl no
      `passwordPolicies`, so the theme says the rules itself (`gen9PasswordHint`; verify.sh fails
      when its length differs from the realm's), and a refused try loses `updatePasswordMessage`,
      so the page remembers a first password for the sign-in's `tab_id` (sessionStorage). Fixed;
      live: the hint before the first try, and after a 14-character try "Use at least 15
      characters." with the label still "Password" and no "Sign out of other devices"; the
      sign-up then finished in the app. b1 checks it.
    - F3, minor: the sign-up page links "How Gen9 uses your data" twice. Fixed: the footer link
      is left out on sign-up, whose card says it (b1d counts one link).
    - F4, docs: a person's budget is "$20 every 30 days" in gen9-learn and gen9-models' README,
      but LiteLLM resets a `30d` budget on the 1st of each month at 00:00 UTC, for everyone at
      once (`duration_parser.py`, `_handle_day_reset`: "Monthly reset on 1st"); a person who
      joined two days before the month ended read a reset on the 1st in Settings. Fixed in the docs.
    - F5, gap: a chat can't be renamed. Its title is its first message's first line (80
      characters), and the chat's menu offers only *Delete chat*, while Claude.ai and ChatGPT
      offer Rename in the same menu (support.claude.com, "How can I delete or rename a
      conversation?"). Plan (Decision Log, "renaming a chat"):
      - [x] gen9-agent: `PATCH /v1/threads/{id}` `{"title"}`: whitespace collapsed to single
        spaces, 1 to 80 characters, else 422; another person's chat or a deleted one 404 (and
        audited, as every owner check); `updated_at` unchanged. Unit tests.
      - [x] gen9-ui: *Rename* in *Chat options*: the title becomes a field in place (Enter or
        leaving it saves, Escape keeps the old one), through a same-origin `PATCH
        /api/threads/[id]`; the header, the tab and the sidebar show the new name at once.
      - [x] Checked live: in Chrome as a person, by keyboard (focus into the field with the name
        selected, Enter saves and focus goes back to *Chat options*, Escape keeps the old name) and
        by mouse (clicking away saves); a blank name keeps the old one; whitespace collapsed; 390
        px fits; after a reload the header, tab and sidebar keep it, and `updated_at` stayed the
        last run's end. Through gen9-ui's route: blank 422 "Name the chat.", 81 characters 422,
        another person's chat 404 (its title unchanged, `thread.access denied` audited), another
        origin 403. `e2e/stacks.mjs` renames before it deletes (all pass); `authz.mjs` covers the
        new route from the OpenAPI document (all pass); reference.mjs names it. Renamed while its
        first answer was queued, the chat kept the name after the answer (database, header, tab and
        sidebar).
    - F6, docs: the page's pointers into the code (`<code>path:line</code>`) drift as code
      changes, and nothing checked them: 6 of 33 were off (`users.py:15` on a blank line, the
      token check's and step-up's ranges, `getSession`'s, Settings' recovery-codes redirect and
      its *Set up*), and F5 moved 4 more, fixed with it. Fixed: each pointer carries the text of its first line
      (`data-at`) and a range that of its last (`data-to`); reference.mjs fails when either has
      moved and says the new line (tried: a pointer set one line off fails with the right line).
      No other doc outside the plans has such pointers.
    - F7, search: meaning search offers chats that aren't about the query. A search for
      "lighthouses" by a person with two chats, neither about it, showed both "by meaning"; for
      "numbers" the chat "Reply with one word: hi" came first. The 0.6-of-best rule
      (`RELEVANT_SHARE`) was measured with text-embedding-3-small and embeddinggemma; `embed` is
      now Qwen3-Embedding-8B, which scores unrelated short texts at 0.45 to 0.56, and the best hit
      is always kept, however far. Probe (scratch, 20 chats, 20 paraphrased queries in English,
      German and Spanish, 14 with no answering chat): with the query as sent today, the right chat
      came first 16 of 20 and the scores overlap (right from 0.368, wrong up to 0.562); with the
      query instruction its model card asks for ("Instruct: …\nQuery: …", documents without; "a
      drop … by approximately 1% to 5%" without it), 20 of 20, right from 0.437, wrong up to
      0.419, and no answering chat at most 0.334. Plan (Decision Log, "meaning search's floor"):
      - [x] gen9-agent: queries in the model's own form and a similarity floor, by the model the
        router says served `embed`; a model not listed gets neither (today's behaviour). Unit
        tests (`test_search_relevance.py`).
      - [x] Live: in Chrome as the second user, Meaning "lighthouses" finds no chat, "numbers" and
        "counting to forty" only the counting chat, "a greeting" only the greeting. `e2e/search.mjs`
        (a paraphrase 0.65, a question sharing no word 0.63) and `past-chats.mjs` pass. b2d: the
        German question still finds the first chat, and a new check, "Which volcanoes erupted
        this year?", finds none. Docs: gen9-agent README ("The query in the model's form"),
        gen9-models README and `config.yaml` next to `embed`, the search spec, gen9-learn's step.
    - F8, bug: text of only spaces passes every `min_length=1`. In Scheduled, a name of spaces
      showed "[object Object]": the form trims it to "", the API refuses it, and gen9-ui's
      `agentJson` passes FastAPI's list of validation errors as the message. Through the API
      (seeded user, `e2e/token.mjs`), a task with a blank name and prompt was created (201,
      both ""), and a message of spaces started a run (202): the model answered it, then each
      of 3 attempts failed at its end (`IndexError` in the executor's title line,
      `"".splitlines()[0]`), 3 model calls, and the run waited for Retry. The web composer
      blocks blank sends; A2A and AG-UI refuse them already ("Send a text part"). Plan:
      - [x] gen9-agent: a message, a task's prompt and name must hold more than spaces
        (`not_blank.py`, pydantic's AfterValidator, as Django's CharField strips before its
        required check), in the runs API and the MCP server's `ask`; the executor's title
        can't fail on an empty first line. Unit tests.
      - [x] gen9-ui: `agentJson` turns a validation list into our own words (a `ValueError`'s
        text) or "Check what you entered and try again."; the task form says "Name the task." or
        "Say what Gen9 should do." before sending.
      - [x] Live: the same API calls now 422 "Name the task.", "Say what Gen9 should do.",
        "Write a message first." (runs and the stream), and no run is started; in Chrome the form
        says "Name the task." and "Say what Gen9 should do.", and a valid task still saves.
        `e2e/mcp-server.mjs` passes with a new check, `ask` with spaces refused. Every place the
        UI shows an API refusal reads it through `lib/refusal.ts` (unit-tested); in client code a
        list there would have been rendered as a React child and thrown.
    - F9, UI: the admin audit log shows six of gen9-agent's action codes as they are, against
      its own rule ("the action codes … never reach the screen"): `thread.delete` (415 events
      here), `account.sweep`, `operator.stop`, `operator.resume`, `restore.account.delete`,
      `restore.thread.delete`; and their actors, Gen9's own (`sweep`, `gen9-agent-stop`,
      `gen9-agent-erase`), as "A person Gen9 no longer knows". Seen in Chrome as the seeded
      admin. Plan: words for each (from what each records: the stop's counts, the sweep's and
      the restore's target), system actors named as the export names them ("Gen9") or as "The
      operator", and a unit test that reads every code gen9-agent records (`record(`,
      `AuditEvent(`, `theirs(`) and fails on one without words. Done: in Chrome the four kinds
      found here read "The operator: Stopped every agent: 0 answers stopped, 0 scheduled tasks
      paused", "Gen9: Removed what Gen9 kept of a person, deleted in Keycloak", "Deleted a chat
      again after a restore: …" and "Deleted a chat". The test finds 34 codes (first 33: a
      query in parentheses hid `run.access` until the kind was read as the last argument) and
      fails with one case removed. `e2e/audit.mjs` checks no row shows a code (50 rows, pass).
    - F10, security, open (decided in item 4): every account, admins included, signs in with a
      password alone; a second step is offered, never asked. OWASP ASVS 5.0 6.3.3 (L2) asks for
      multi-factor authentication to reach the application, or "a fully documented rationale
      and a comprehensive set of mitigating controls" (V6, fetched), and Gen9's docs
      state neither. Seen as the seeded admin in Chrome. To weigh in item 4: a second step
      required for `gen9-admin` (Keycloak's conditional flows by role), what it costs the checks
      that sign the seeded admin in, and the rationale for people.
    - F11, search: the connector directory's "github" put GitHub's own server
      (`io.github.github/github-mcp-server`, "GitHub") 123rd of 10,959 matches, after PCFHub,
      AIXBT and Hebcal. Half the registry's names start `io.github.<user>/`, which says only
      that the publisher signed in with GitHub to publish (modelcontextprotocol.io/registry,
      "Authentication"), and trigram similarity favours short names. Measured over 11 searches
      (github, cloudflare, slack, notion, linear, stripe, sentry, postgres, google drive, web
      search, figma): matching and ranking on the name without `io.github.`, with a server whose
      publisher namespace holds every word first (the docstring's own aim, "a publisher's own
      `com.cloudflare.mcp/mcp` first"), then an exact title, then similarity, puts GitHub's,
      Cloudflare's, Notion's, Linear's, Stripe's and Figma's own first, and leaves the rest as
      they were. Plan:
      - [x] gen9-agent `api/directory.py`: that match and order; the trigram index still serves
        the first filter. `e2e/directory.mjs`: "github" finds GitHub's own server first.
        Live through the API: github, cloudflare, notion, linear, stripe, figma and "github
        issues" each find their service's own server first, the others as before, in 9 to 132
        ms; `e2e/directory.mjs` passes with the new check (it first read the old list and kept
        "github" in the field: both fixed in the check).
    - F12, gap: a person can't see or take back the apps they allowed to act for them. The
      terminal (`gen9-cli`), MCP clients and A2A agents (`gen9-mcp`, "Agents (MCP and A2A)") and
      clients that register themselves each ask for consent, and Keycloak keeps it, but Gen9's
      Settings shows none of it: the only place is Keycloak's own account console
      (/realms/gen9/account, "Applications"), unthemed and linked from nowhere in Gen9. Seen in
      Chrome as the second user. GitHub ("Authorized OAuth Apps") and Google ("Third-party apps
      with account access") put this in their own settings. Probe on the running stacks: the
      second user signed the terminal in (device flow, Allow), then Keycloak's `DELETE
      /users/{id}/consents/{client}` ("Revoke consent and offline tokens", its Admin REST API):
      204, the client left the person's session (`gen9-ui+gen9-cli` → `gen9-ui`), and the
      terminal's refresh token was refused (`400 invalid_grant`); the web session stayed.
      Plan (Decision Log, "apps with access"):
      - [x] gen9-ui: Settings, "Apps with access", from Keycloak's Account REST API with the
        person's own token (`/account/applications`, and `DELETE …/{clientId}/consent`), as
        Settings already reads and ends sessions (`lib/auth/account.ts`): no admin power, and
        only the person's own consents. Each app with what it may do in the consent screen's own
        words, and *Remove access*, which asks first. (A first cut in gen9-agent through the admin
        API was dropped before committing: more power than the job needs.)
      - [x] Live: in Chrome as the second user, the empty state, then Gen9 CLI listed after a
        device sign-in ("Can see your name; see your email address; see your role in Gen9
        (member or admin). Allowed" with the date), *Remove access* asks, and after it the list
        is empty, the terminal's refresh token refused (`400 invalid_grant`), and `gen9 whoami`
        says "your sign-in ended. Run `gen9 login`" once its access token lapses. The account
        API's scope `name` is the consent text (a message key for Keycloak's own scopes, the
        words for Gen9's), so the UI words the four keys as the theme does
        (`lib/app-scopes.ts`, tested against i18n.ts and configure.sh). `e2e/mcp-server.mjs`
        step 8: both agents listed and removed, one with a URL for a client id, refresh 400 and
        400. gen9-learn b5: listed after `gen9 login`, and still after `gen9 logout`, which
        revokes its tokens but not the consent (first written as gone: the check was wrong, not
        Gen9). Docs: settings spec, information architecture, auth-architecture, Keycloak and
        e2e READMEs.
    - F13, privacy notice: the privacy page (GDPR Art. 13) named "the language models that write the
      answers" as where questions go, but each finished question and answer is also sent to the
      embedding model to make chats searchable by meaning (`runs/indexing.py`), and so is a search
      by meaning, through OpenRouter under the same `data_collection: deny`. And it sent people to
      "Settings, Profile" to change how they sign in, which is Sign-in and security. Checked and
      right: sign-in records 30 days (`eventsExpiration` 2592000), work in progress 72 hours,
      trace copies within two days, no training for every OpenRouter model; the router's
      `speak`, `transcribe` and `image` aliases go to OpenAI directly but nothing a person does
      reaches them. Fixed: the embedding model named, the sign-in and Apps with access pointers
      (gen9-learn's b1-4 says the same). Live: /privacy reads so.
  - [x] 2. An independent reader: gen9-learn/index.html as a newcomer; every code pointer and
    claim checked against the code. Sonnet read all 3,195 lines and checked about 110 claims in
    the prose (numbers, names, paths, flows) that nothing checks mechanically: none wrong. Its six
    findings, each checked:
    - R1, kept: Temporal's Signal and Update carry the environment's and a deletion's story
      (line 1368 on) but aren't explained. Added to the cast, from Temporal's docs (a Signal
      isn't answered; an Update's sender gets a reply or an error) with Gen9's own: `answered`
      and `end` are Signals, `acquire` and `deleted` Updates (the workflows' decorators).
    - R2, kept: "task" means four things (scheduled, background, MCP, A2A) with no entry. Added,
      each linked to its step: all four are a run in a chat.
    - R3, kept: "View" first appears in a verified output. The step's Predict now names it, as
      the MCP Apps spec does ("the View is the UI running inside a sandboxed iframe").
    - R4, kept in part: "12 numbered codes" and "12 of 12 left" are Keycloak's default and
      Settings' words, observed by b3 (`recoveryCodeCount` 12) but never asserted. b3 now
      checks both.
    - R5, rejected: SearXNG's defaults (DuckDuckGo, Brave, Wikipedia) are true of the running
      SearXNG: its `/config` lists them enabled, with Google, Yahoo and Bing.
    - R6, rejected: CVE-2026-88770 "fixed for 26.7.5" is right: keycloak/keycloak#52783 closed
      2026-09-17 labelled `release/26.7.5`; 26.7.4 is still the latest release (gh),
      and #51275 is still open.
  - [x] 3. Docs against code: every README, `docs/*.md`, AGENTS.md. In two agents, one after
    the other. 3a (Sonnet): gen9-agent, gen9-models, gen9-cli and gen9-sandbox's READMEs,
    `docs/temporal.md`, `secrets.md`, `auth-architecture.md`, `ai-act.md`; about 600 statements,
    16 findings, each checked by me, all kept (one refined, one only in part), fixed together:
    - D1: `temporal.md` listed memory consolidation, an entity workflow nothing runs, decided
      against: moved to "Not used yet", with the decision.
    - D2: the README's requirements and network loop left out gen9-sandbox, which its compose
      file needs.
    - D3: Stop "within about 2 s" and "about 3 s" (and the API's own summary, and gen9-learn's
      Reference, which quotes it), while `temporal.md` reasoned half a second. Measured three
      times on the running stacks: 0.29 to 0.32 s from the cancel to `cancelled`. All now say
      half a second.
    - D4, D5, D12: an account's deletion order in `temporal.md` had sessions after Langfuse, and
      both deletions there left out environments and the account's tasks'
      Schedules, which the README's list left out too (`workflows/deletion.py`).
    - D6: permanent failures left out 422 and a context budget overflow (`PERMANENT_ERRORS`).
    - D7: `execd` "bound to loopback": on Docker Desktop; on Linux the bridge's gateway.
    - D8 (refined): a person's own budget through `/customer/update` with `max_budget` works but
      sets no period, so it never resets; e2e uses `/budget/new` then `/customer/update`.
    - D9, D10, D11: the reranker's memory (50 MiB loaded, 175 serving: the probe's numbers),
      `ready` also waiting for `searxng`, and the `reranker` service missing from the table.
    - D13 (in part): `auth-architecture.md`'s token claims (`nbf`, not `iat` and `sub`); the
      README's list was right.
    - D14, D15, D16: the inputs row without `{"responses"}`, the API table without `GET
      /v1/directory`, and A2A's `A2A_WAIT_S` unmentioned.
    - D22 (late, from one of 3a's sub-agents): `auth-architecture.md` said Keycloak adds
      `temporal` to `aud` only for users with a Temporal role, without saying why; it's the
      realm's `roles` scope's `oidc-audience-resolve-mapper` (read from the running realm). Cited.
    3a split its reading across 8 sub-agents at once, against the owner's "never in parallel";
    my brief hadn't forbidden it. Every later brief does.
    3b (Sonnet, told to start no sub-agent): the root README and Makefile, the AGENTS.md files,
    e2e/README.md against all 49 scripts, the other stacks' READMEs, `docs/design/` and
    `docs/PLANS.md`; about 140 statements, 5 findings, all kept:
    - D17: the README named `harness.md` the current plan; it's `gen9-learn.md` (AGENTS.md).
    - D18: gen9-ui's README asked for Compose 2.22+, while `make doctor` enforces 2.24.0.
    - D19 to D21: three design docs call the account menu's admin item "Users"; the menu said
      "Manage users", the sidebar and the page "Users". Fixed in the menu (it was the first
      label, from before Plugins and Audit log came as plain nouns), which makes the docs right.
  - [ ] 4. gen9-agent's code: correctness, security, async rules, error handling. In three
    agents, one after the other (4a the edges people and programs reach, 4b what the agent can do,
    4c runs, workflows and deletion). 4a (Sonnet): auth, every API module, MCP, A2A, AG-UI,
    connectors' sign-in and network guard, the middleware, audit, export; ownership checks
    (404 on another's id), each surface's audience and scope, parameterized SQL and the async rule
    all found sound. Its findings, each checked:
    - F15 (4a-1), kept: the network guard took `is_global` as public, and Python counts all of
      NAT64's `64:ff9b::/96` as global whatever IPv4 it carries (and the deprecated `::/96`):
      `64:ff9b::169.254.169.254` passed, in the API container's Python 3.12.14, for connectors and
      plugin sources alike, and through a NAT64 gateway would reach a cloud's metadata service.
      Fixed: `connector_net.is_public`, which judges the IPv4 they carry (a public one stays
      public, as DNS64 synthesizes on IPv6-only networks), used by both. Tests fail without it.
      Live: three such connector URLs refused (422, "on a private network"), and a plugin
      source in Chrome as the admin ("64:ff9b::a9fe:a9fe is on a private network").
    - F16 (4a-2), kept: a View's tool call relayed its server's answer to the browser without
      the 5 MB bound its resource reads have. Fixed, same bound; tested with the real
      in-memory MCP server.
    - F17 (4a-4), kept: an A2A task read after its run was deleted (its chat deleted meanwhile)
      failed an `assert`, a 500; now `TaskNotFoundError`. Tested.
    - 4a-3, rejected: no "last admin" guard on demoting another admin. Only two admins demoting
      each other at the same instant could leave none, and a check before demoting wouldn't
      stop that (each still sees the other); Keycloak's bootstrap admin restores the role.
    - 4a-5 was a note, not a finding (the middleware order, found right).
    - F10, decided: 4a found the browser flow's second step is conditional on one being set up,
      admins included, with real compensating controls (15-character minimum, the NCSC 100k
      blocklist, lockout after 5 tries) but no written rationale, which ASVS 6.3.3 asks for.
      Now: the rationale and controls go into `docs/auth-architecture.md`. Next, as its own
      milestone (M10) rather than inside an audit: a second step required of admins (enrolled
      when made admin, the seeded admin's set up by `make setup`, every check that signs the
      admin in taught it), since it changes Keycloak's flows and a dozen checks.
    - F14, found by me (export): Settings' *Download a copy* leaves out two things Gen9 keeps
      about the person: the apps they allowed (Keycloak's consents) and their sign-in records
      (Keycloak's events, kept 30 days, as the privacy page says). GDPR Art. 15 covers both;
      Google's and GitHub's exports include access and security logs. An earlier decision
      (manual-e2e.md, B5) had sent people to the operator for sign-ins, with no reason recorded;
      apps with access didn't exist then. Fixed: `apps.json` (consents) and `sign-ins.json`
      (when, what, which app, which address, why one failed) in the export, through gen9-agent's
      admin client, whose service account gains `view-events` (the realm file, and
      `configure.sh` for realms imported before; it already held the broader `manage-users`).
      Live: the seeded user's export has both (1,513 sign-in records, gen9-cli among the apps), and
      no token or secret field; Settings' row names them. Unit tests (the parts, their fallback,
      paging). b6d's new check runs in the closing full run (it needs the earlier batches).
    - F21, found by me (a chat's name): a chat is named from its first message only when that
      answer succeeds (the executor), so a first answer waiting for Allow, or failed, left "New
      chat" in the title bar and in the sidebar, where "New chat · Needs you" can't be told from
      another. Seen in Chrome as the second user. Plan: name it when its first run is queued
      (`runs/store.py` `enqueue`, in the same transaction, only while it's still "New chat", so a
      rename, a task's name or a background task's title stays). Done, with a unit test on the
      statement. Live: a first answer waiting for Allow already shows "Remember that I drink
      green tea. · Needs you" in the sidebar, and the same in the title bar.
    4b (Sonnet, alone): what the agent can do (tools, connectors, environments, plugins and
    skills, memory, approvals, background and scheduled tasks, grading, the model router), mostly
    against the Agentic Top 10. Its findings, each checked:
    - F18 (4b-1), kept, serious: Deep Agents' `delete` tool (there since 0.7.18) was never gated,
      so in "Ask before acting" the agent deleted the person's memory with no Allow. Live, as the
      second user: "Use your delete tool to delete /memories/AGENTS.md" deleted it (the store row
      gone). Fixed: `delete` gated as memory writes are; a test fails if Deep Agents adds a file
      tool that writes (`_FILE_MUTATION_TOOLS`) the gate doesn't see; both tests fail without the
      fix. Live after: "Gen9 wants to use delete … /memories/AGENTS.md", Deny, "You declined",
      the memory kept.
    - 4b-2, rejected: files the agent writes in its own environment don't wait in "ask" mode.
      They change nothing outside the chat's sandbox (a command can: it reaches the hosts the
      environment allows, so `execute` waits); approvals.py now says so.
    - F19 (4b-3, 4b-4), kept: a background task's answer and a grader's findings reached the
      chat as a plain message, while a trigger's text came labelled as data. Both can carry what
      a page or file said. Fixed with one helper (`as_data.py`, from `tasks.with_payload`): the
      answer in `<task-answer>`, the findings in `<grader-findings>`, each with a note that it's
      data, which the text can't close; the grader is told what it grades isn't instructions to
      it; the web app shows a notice without the marks (`lib/notice.ts`, older notices as they
      were). Unit tests on both sides.
    - 4b-5, rejected: plugin connectors' failure times are kept per person, plugin and server
      until one succeeds; bounded by those counts, not by anything a caller sends.
    - 4b-6, rejected: without Keycloak admin credentials, standing can't tell a disabled person
      and lets their work run; documented, and every install has the credentials (`make setup`).
    - F19's follow-up, found by me in the closing sweep: the grader's findings, now in a block,
      showed in the chat as the person's own message with the marks in it. The revision run is
      marked (`revision` in its input, as a notice's `notice_of`), the thread detail says so on
      the message and the active run, and the web app shows it as *From the rubric check*
      without the marks (`lib/notice.ts` `revisionText`). e2e/outcomes.mjs and b2wd check it.
    4c (Sonnet, alone): runs, workflows, workers, deletion and data lifecycle. Durability,
    idempotency, determinism and the async rule found sound. Its findings, checked, and not
    fixed: the owner stopped the audit here to close M9. They are the first of the
    backlog below.
  - [ ] 5, 6, 7: not run yet. The owner stopped the audits after item 4 ("can we
    stop the audits, fix the findings so far then finish the closing point"), then resumed them
    the same day ("lets complete the full thing, the thing i stopped you for backlog, bring those
    back and keep working on the full thing"). The backlog below is M9's remaining work, in its
    order, then M10; each item is checked here when verified live:
    - [x] F22, deletion never gives up (backlog 1). Steps have no schedule-to-close limit (a
      test of a nine-day outage failed before, passes after); `make doctor` warns of a deletion
      running over a day; gen9-temporal keeps a retrying step's failure up to 64 KB, so the UI
      shows why. Live: 40 recorded deletions replay with the new code; Langfuse's
      API stopped, `real2-…@gen9.test` deleted in Settings (`DELETE /v1/me` 202), the deletion
      waited on `erase_traces` (scheduleToClose 0s, attempt 8, its error "ConnectError: [Errno
      -2] Name or service not known" once the setting applied, "Failure exceeds size limit."
      before), doctor's check (threshold moved to now) warned of 3; Langfuse started
      and the data and Keycloak user were gone 5 minutes later, on the next try.
    - [x] F25, found proving F22: on a 202 the person was told "Your account was deleted. Your
      chats, … are gone from Gen9." and an admin "User deleted with all their data.", while the
      deletion was still under way (the user stays listed, disabled). Now a 202 lands the person
      on `/signed-out?reason=deleting` ("Your account is being deleted. Nobody can sign in to it
      now. … which finishes on its own.") and tells the admin "Deleting the user and all their
      data. It finishes on its own; until then they show as disabled." Live (Langfuse's
      API stopped): both texts after 15 s, the row Disabled; both deletions finished 40 s after
      Langfuse's return; with Langfuse up, the 204 texts unchanged. b7d checks the person's path
      (the page's claim that a deletion with Langfuse down answers 202 and finishes later had no
      check until now).
    - [x] F24, late trace passes for users deleted in Keycloak (backlog 2). Every account deletion
      runs the late passes (`late-erasures-always`), and the sweep starts its deletions and leaves
      them (`start_child_workflow`, `ParentClosePolicy.ABANDON`, `sweep-starts-deletions`). Two
      tests fail on the old code and pass: the sweep ends with its deletions running, and a
      deletion without Keycloak erases late. (The time-skipping test server hangs when time is
      skipped across a timer then an Activity in an abandoned child, a probe showed, so the
      sweep's test checks its children in real time and not to their end.) Live:
      40 recorded deletions and sweeps replay with the new code; `real-…@gen9.test` deleted in
      Keycloak directly, the sweep, triggered by hand, ended in 0.34 s, its deletion ran
      on with no Keycloak step, the data gone, and finished by itself 11 minutes later, after both
      late passes (timers of 60 s and 600 s, `erase_traces` three times).
    - [x] F23, a "done" or "waiting" email lost to a crash (backlog 3). A repeated attempt that
      finds the run succeeded sends "done", and one of a paused turn still unanswered sends
      "waiting"; `notify` sends a notice once per run and kind, so a notice already sent isn't
      again. Tests of both paths fail on the old executor and pass. Live (Alan, a
      one-off task run now): "F23 check is done" emailed; its `run_notices` row removed (the
      state a worker stopped between the two leaves), the executor's repeated attempt run in the
      worker against the real database and Mailpit sent it (235 to 236 mails), a second attempt
      sent nothing; the task, its chat and the notice setting put back.
    - [x] 4c-4, a regression test for answered ids (backlog 4). A sequence found:
      LangGraph 1.2.12 gives two interrupts raised one after the other in the same task the same
      id (a probe: `9a241dec…` for both, and resuming by that id twice answers them in order). An
      MCP server that asks again when called with its first answers (multi-round elicitation,
      allowed by MCP 2026-07-28) raises its second interrupt in the same tool call, so the turn
      comes back asking for "the same set": `workflows/runs.py` then drops the id as unanswered
      and waits for a new answer, which the API refuses as already given (first wins), and the run
      waits until it expires. Reproduced live with `book_table`, a
      two-round tool added to `e2e/fixtures/elicit_mcp.py`, through the API as the rehearsal
      person: round 1's form arrived (`input.requested`) and was answered; the tool's second
      question put the run back to `waiting` with no new `run_inputs` row and no
      `input.requested` (the insert met the answered row: `on_conflict_do_nothing`), so the person
      saw nothing, and answering under the same id got 409 "Already answered". `langchain.mcp`'s
      `_call_tool_with_interrupts` raises one `interrupt()` per round inside the same tool call,
      which is why the ids match. Fix designed: each round its own request id (the first keeps
      the interrupt id, later ones `<id>-<round>`), chosen from the run's `run_inputs` (at a
      turn's start the latest round for that id, answered or not; at its end a new round when the
      latest was answered, as this turn resumed with it), and resumed as `{interrupt id: answer}`,
      which LangGraph matches to the next `interrupt()` of the task in order (the probe). Deploy
      after the owner's demo (it touches the pause and resume path the demo's approvals use).
      A second probe settles which round a paused task is at, even for a repeated attempt after a
      crash between the checkpoint and `store.wait` (where "the latest request is answered" can't
      tell "resume round 1" from "round 2 not recorded yet"): LangGraph keeps the answers a task
      already consumed as that task's `__resume__` pending write (`['A']`, then `['A', 'B']`), so
      the round is 1 + its length; read it from the checkpoint (`aget_tuple(...).pending_writes`,
      per task id, and for a subagent's task in its own checkpoint namespace) rather than infer it.
      Fixed so (`runs/executor.py`, `rounds`; `store.round_id`), with tests on real graphs, a
      subagent's included (`tests/test_interrupt_rounds.py`). Deployed, with the
      demo's flows re-walked on it after: live, the same repro now gave round 2
      its own request (`…-2`, its `input.requested`), and "Booked a table for 4 on Friday at
      19:00."; `e2e/elicitation.mjs` gained the two-round case (a card each, both answers used)
      and passes; gen9-learn's b1 b2 b2d b2w b2wd b2x b2xd b5 b5x, re-walked on the new code, 85
      of 85 (approvals, questions and a single-round server question among them).
    - [x] 5. The gen9-ui audit (a Sonnet agent, alone, read-only). It
      checked the sign-in flow, sessions, cookies, back-channel logout, CSRF, admin checks, SSRF,
      markdown, MCP Apps' sandbox, downloads, headers and the image, and found them sound. Two
      findings, both checked live:
      - [x] U1, high: a connector's server asking the person to open an address (MCP elicitation,
        URL mode) is opened as given. Live (the rehearsal person, a test tool `open_notes` added
        to `e2e/fixtures/elicit_mcp.py`): the card showed `javascript:alert(document.domain)` with
        *Open it*, and clicking handed that string to `window.open`; the MCP SDK types the URL
        as `str` and gen9-agent passes it on. The terminal is worse: `gen9` hands it to the
        system browser (`webbrowser.open`), which launches a `file:` path or another app's scheme
        once the person says y. Fix written (both clients open only http and https: the card
        shows no Open and says to decline; the terminal declines unasked) with tests. Live after
        deploying: the card read "This isn't a web address, and Gen9 opens only those
        (http or https). Decline it." with no Open, and `window.open` was never called; `gen9 ask`
        said "Gen9 opens only web addresses (http or https), so it declines this one." and the run
        finished; `e2e/elicitation.mjs` gained both cases and passes (13 of 13).
      - [x] U2, medium: `/api/csp-report` (open to anyone, as browsers send reports without
        cookies) logged a report's directive as given: one unauthenticated POST wrote a forged
        second log line (seen live in gen9-ui's log). Fixed: the directive must look like a
        directive name, and control characters never reach the log; tests. Live after deploying:
        the same POST logs one line, `[csp] (directive?) blocked … on /chat`, and a genuine report
        beside it logs as before. Its shared quota of
        60 lines a minute, which a flood can use up: kept, rejected as a finding, since dropped
        reports are counted in a line of their own and a report endpoint can't tell a browser
        from anyone else.
      - D19, fixed: AGENTS.md's Checks run `uv run ty check src` in gen9-cli, which had no `ty`
        (Failed to spawn). gen9-cli's dev group has it now (`ty>=0.0.83`, as gen9-agent's), and
        `uv run ty check src` passes there.
    - [x] 6. The other stacks and scripts (a Sonnet agent, alone, read-only). It found the Keycloak realm and configure.sh, the theme's sanitizing, the router's
      hardening and admin API, the sandbox server and SNI binding, Temporal's TLS and authorizer,
      Langfuse's ports, Postgres's roles, and the root scripts' destructive paths sound. Three
      findings, checked live:
      - [x] S1, reported critical, medium here: the sandbox egress's `deny.always` had no IPv6 form
        of the addresses it denies (IPv4-mapped `::ffff:0:0/96`, NAT64 `64:ff9b::/96` and
        `64:ff9b:1::/48`), the class F15 fixed for connectors. Live, from inside a sandbox:
        `::ffff:169.254.169.254` behaves as the plain address (a dual-stack socket sends it as
        IPv4: no answer, as for every host not allowed), and NAT64 has no route ("Cannot assign
        requested address"): no path on this install, but one on an IPv6-enabled network with a
        NAT64 gateway, for an allowed name resolving to such an address. Fixed: the three
        prefixes added; a sandbox made after the image's rebuild logged "loaded 11 always-deny
        rule(s)" and its nft ruleset holds `::ffff:0.0.0.0/96`, `64:ff9b::/96`, `64:ff9b:1::/48`;
        the metadata address, plain and mapped, still gets no answer.
      - [x] S2, medium: Langfuse's vendored compose file falls back to published defaults (SALT
        `mysalt`, ENCRYPTION_KEY all zeros, …) when its .env lacks a key; `init-env.sh` writes
        them all, so only a damaged .env reaches it. This install: all ten set (lengths only
        read). Fixed where Gen9's start checks live: `scripts/doctor.sh` (and so `make up`'s
        preflight) fails when any is missing. Live: "ok gen9-langfuse/.env holds each of
        Langfuse's secrets"; on a scratch copy whose .env had ENCRYPTION_KEY empty, the preflight
        failed with exit 1.
      - S3, low, rejected: e2e's test servers listen on 0.0.0.0. gen9-agent reaches them from its
        containers at host.docker.internal, which on Linux arrives from the Docker bridge, not
        loopback; they run only during a check and hold no real secret.
    - [ ] 7. Tests against the features (a Sonnet agent, alone, read-only).
      It mapped features to checks and reported ten gaps from e2e/; checked against every check,
      four were covered by gen9-learn's verifier, which it hadn't credited: backup, wipe and
      restore (b7), NUL 422 and oversized 413 on the live API (b7d), sign-up end to end (b1),
      the web app's step-up with a real 5-minute wait (b6). What stands, and what it found:
      - [x] F27, found by the new check below, serious for the demo: gen9-agent's service token
        for Temporal and Keycloak's Admin API was judged fresh by the monotonic clock. The laptop
        slept for 33 minutes (`pmset -g log`), Docker's VM paused, its monotonic clock
        lagged wall time, and for minutes after waking the API kept a token Temporal refused:
        47 calls in 29 s (every scheduled task and secret the check made)
        got 500, Temporal's log "Authorization error … Token is expired". Chats and task firings
        after a wake would fail or wait the same way. Fixed: the expiry is wall time, and the
        token is checked every 15 s (a cached read until it's due). Tests (a wake: wall time on
        10 minutes, monotonic still) fail on the old code and pass; live after deploying, a
        chat, a task and a secret made and removed, no refusal in the log. `caffeinate` keeps
        this Mac awake until after the owner's demo.
      - [x] T2, step-up for a client that isn't the web app: covered now by `e2e/admin-api.mjs`
        (a terminal sign-in over 5 minutes old gets 401 insufficient_user_authentication with
        max_age 300 on `DELETE /v1/me`, the account kept; a fresh one deletes it); passed live.
      - [x] T3, T4: an admin deleting a person, and sending a password reset, driven by no check;
        both are web-app actions (decision 11: the terminal is never an admin, so the new check's
        first run, as a terminal admin, got 403 by design). `e2e/admin-api.mjs` drives them in
        Chrome as Ada: the toast, the email in Mailpit, gone from Keycloak and Gen9, each audited;
        passed live.
      - [ ] T7, T8, found: `POST /v1/admin/directory/sync`, `/v1/admin/search/reindex` and
        `/v1/admin/users/remove-deleted` are reachable by no client: the web app has no page that
        calls them and the terminal is never an admin. Decide after the demo: admin buttons, or
        drop the routes (the Schedules run them anyway).
      - [x] T1, a person's caps, tasks and secrets: the 11th task and the 101st secret refused
        (409), the first 10 and 100 kept (`e2e/admin-api.mjs`, passed live after F27's fix).
      - [x] T1, connectors: the 51st refused (409), the first 50 kept, on e2e's elicitation test
        server (`e2e/admin-api.mjs`, passed live).
      - [ ] T1's rest: the files' cap (10 GiB a person, 413).
      - [ ] T6, the database unavailable (503 with Retry-After), and T9's re-deletion after a
        restore, only manual so far; both disruptive, after the demo.
      - [x] T10's rest: a plain forgot-password (no second step): `e2e/recovery.mjs` step 5, the
        link straight to a new password, then signed in, `UPDATE_PASSWORD` logged; passed live.
    - [x] Housekeeping (backlog 7), kept: `lib/audit-words.test.ts` and `lib/app-scopes.test.ts`
      read gen9-agent's and gen9-keycloak's files, which is what makes them guards (the audit log's
      words and the consent screens' words can't drift from the source). CI checks out the whole
      repository (`actions/checkout`, no sparse checkout) and runs them from `gen9-ui`, so they
      hold there; the image doesn't run tests. Revisit only if the stacks move to repositories of
      their own.
    - [ ] Closing: gen9-learn's full run, `page.mjs` and `reference.mjs` pass; the plan and PR #2
      in step. (The closing run started before the owner resumed the audits, checks
      what was committed by then.)

## M9 backlog (logged when M9 stopped; resumed the same day)

Picked up in this order. Each is its own verified unit, as M9's were.

1. **F22, deletion never gives up (4c-1, critical).** `workflows/deletion.py` runs every step
   with `schedule_to_close_timeout` of 7 days (`Step.schedule_to_close`). A step that keeps
   failing (Langfuse or the router's admin API down, or a rotated key) raises after a week and
   the whole `DeleteAccountWorkflow` fails before `DELETE_USER_DATA`: the person's chats and
   row stay, while the API had answered 202. Fix designed and tried, then parked unverified:
   `schedule_to_close: timedelta | None = None` (retry until it succeeds; retries add no history
   events, and Temporal's determinism check ignores timeouts), and the README's "for up to a
   week" corrected. Prove it live: stop Langfuse, delete a throwaway account, and see the
   deletion wait on `erase_traces` past the old limit (the time-skipping test environment), then
   finish once Langfuse is back. Consider showing deletions running over a day in `make doctor`.
2. **F24, late trace passes for users deleted in Keycloak (4c-3).** `DeleteAccountWorkflow` skips
   `_late_erasures` when `keycloak=False` (the sweep's deletions), so a trace an answer still
   running at deletion lands after the first pass and stays in Langfuse. Designed: run the late
   passes for every account deletion behind `workflow.patched("late-erasures-always")`, and have
   `SweepDeletedUsersWorkflow` start its deletions (`start_child_workflow`, `ParentClosePolicy.
   ABANDON`, behind `workflow.patched("sweep-starts-deletions")`) rather than await each for ten
   minutes. Its test must advance the clock (`env.sleep(timedelta(minutes=15))`) rather than
   `handle.result()` a child: the time-skipping server found no completion event that way.
3. **F23, a "done" or "waiting" email can be lost (4c-2).** In `runs/executor.py`, the notice is
   sent after `writer.succeed` or `writer.wait` commits; a worker killed between the two never
   sends it, because the retried attempt returns early (`store.start` sees the run final, or
   `store.responses` finds it unanswered). `run_notices`' key makes `notify_safely` idempotent,
   so send it again on those early returns, as `finish_run` does for failures.
4. 4c-4, suspected, no sequence found: `workflows/runs.py` drops answered ids when a turn resumes
   for the same set twice; add a regression test that an id answered in Postgres is never
   dropped.
5. **M9 items 5, 6 and 7, not run:** the gen9-ui audit, the other stacks and scripts, and tests
   against the features (what nothing checks). Same method: a Sonnet agent at a time, told to
   start no sub-agent, each finding checked live before a fix.
6. **M10, a second step for admins (F10),** below.
7. Housekeeping: `lib/audit-words.test.ts` and `lib/app-scopes.test.ts` read gen9-agent's and
   gen9-keycloak's files from gen9-ui's tests; keep them if CI stays a monorepo checkout.

- [ ] M10 A second step for admins (from M9's F10). Every member of `admins` must have an
  authenticator app or a passkey: asked to set one up when made admin (a required action, from
  Gen9's admin API and `configure.sh` for existing admins), the seeded admin's set up by `make
  setup` with its secret kept for the checks, and every check that signs the admin in (e2e and
  gen9-learn) answering the second step. Research first: Keycloak 26.7's conditional flows by
  role or group, and how leading self-hosted products require it of admins.
  - Found (Keycloak 26.7.4's source): `ConditionalOtpFormAuthenticator` takes a
    `forceOtpRole`; for a user holding it, OTP is required, and one without OTP gets the
    `CONFIGURE_TOTP` required action (`setRequiredActions`, line 306), so an admin sets up an
    authenticator app at their next sign-in. A role-conditioned subflow ("Condition - user
    role") can instead ask for OTP or a passkey. Still to weigh: a passkey admin's path through
    Gen9's browser flow, and Keycloak's own admin console (master realm) staying separate.
  - Design (from Keycloak 26.7.4's own docs, `server_admin/topics/authentication/
    conditions.adoc` at tag 26.7.4, "Conditional 2FA sub-flow with OTP default": "if the user has
    none of the 2FA methods configured, the OTP setup will be enforced to continue the login"; and
    its conditions: `Condition - User Role`, `Condition - sub-flow executed`). In Gen9's browser
    flow's forms sub-flow, after the existing conditional second step (authenticator app, passkey,
    recovery codes, each alternative, `Condition - user configured`), a conditional sub-flow
    "Admins need a second step": `Condition - User Role` = `gen9-admin` and `Condition - sub-flow
    executed` (the second step not run), then `OTP Form` required, which sets up an authenticator
    app at that sign-in. Everyone else is untouched; an admin with any second step signs in as now.
    Built by `configure.sh` (so existing realms get it at their next start), with `verify.sh`
    checking it. To probe first, in a throwaway Keycloak 26.7.4 (its own Compose project, never
    the running stack): a passkey-only admin (the passwordless path skips the forms sub-flow?), an
    admin made while signed in (the next sign-in, or Gen9's admin check too?), and a person removed
    from admins (unaffected). The seeded admin then needs a second step: `make setup` gives Ada an
    authenticator whose secret it keeps in gen9-keycloak/.env for the checks (e2e and gen9-learn
    sign Ada in, and the owner's demo page). Not before the owner's demo: it changes how Ada
    signs in.

## Surprises & Discoveries

- The same closing run failed b5xd: `search_chats` for "lighthouses" in keyword mode returned
  its default 5 hits without the first chat, which the check expected among them. By then the run
  user has at least 7 runs naming the word, and keyword search is BM25 over each run's question and
  answer, which favours short texts: b5x's one-line "lighthouse-mcp/agui/a2a" replies outrank
  part 2's paragraphs on lighthouses. Gen9 is right; the check assumed a ranking. It asks for 20
  hits now, and the page says what the default is and why the order is so.
- M9's closing full run failed once in b4: after *Sign out* on the other browser,
  Puppeteer waited 30 s for the page's network to go quiet and it never did. The sign-out itself
  had worked: Keycloak's back-channel reached gen9-ui at the click (its log), and
  the failure screenshot shows only *This browser* left. A rerun of b1 b3 b4 passed. The cause
  of the busy network wasn't pinned down (Settings has no poll of its own); the full run's user
  has about 15 chats in the sidebar by then, the rerun's none. b4 now waits for the toast a
  person sees (*Signed out …*) and requires it, as b3 does, instead of network idle.

- The pointer guard (F6) paid off at once: F8's commit touched gen9-agent and gen9-ui only, so
  gen9-learn's checks weren't run, and it moved 7 of the page's pointers (an import or a
  validator above them). The next `reference.mjs` named each with its new line. AGENTS.md's
  Checks now ask for `reference.mjs` after any change to gen9-agent, gen9-ui or gen9-cli code.

- The first run of `reference.mjs` counted 33 services, not 40: gen9-langfuse's Compose file is
  `docker-compose.yml` (Langfuse's own name), not `compose.yaml`. The check reads both names now.
- The page named 5 of 12 workflows, 2 of 34 Activities, 1 of 4 Schedules, 8 of 28
  tables and 14 of 23 make commands: "exhaustive" wasn't measurable until the check existed.
- A search by meaning waits for the router to embed the query, and the provider was slow: 5.7 s and 23.5 s for single words (`curl` to gen9-models' `/v1/embeddings`), where
  e2e measured 0.7 to 13 s (P3-D9). A 30-second wait in b2d timed out on a Search page still showing
  its placeholders; the checks now wait up to 120 s.
- `page.goto(…, networkidle0)` never settles on a chat whose run is waiting (its event stream stays
  open): b2d timed out there after the limit's card. Load such a page with `load`.
- A file attached in the composer is kept with `origin` `upload`; one the environment shares,
  `output` (`chat_files.py`).
- A one-word query by meaning lands nearest the shortest rows, whatever it means: "Leuchttürme"
  ranked "Reply with one word: over" first and the lighthouse chat outside the top five on a
  fresh user (the full run), where run 3 had it first. A German question, "Welche
  Türme warnen nachts Schiffe?", ranked the four lighthouse rows first (cosine distances 0.44 to
  0.50, measured with the router's `embed` and pgvector's `<=>`). b2d and the page use the
  question, and the page says why.
- An answer as the page shows it starts with its steps' summary ("Used 2 tools, Asked a
  helper: …"), which grows when the model delegates: b2xd's OAuth check tested the first 120
  characters and missed the note that followed. Checks test the whole answer, and the tool's
  result in `run_events`, and record only the matching part.
- Full run 2 stopped in b2xd: asked to "use" the elicitation server's tool, the model asked its
  own question first (a run waiting with kind `question`), and the check waited 3 minutes for the
  server's card. The prompt now says to call the tool without asking first, and the wait ends on
  either card, failing at once with what the chat did.
- `docker compose run … | tail -1` returned nothing in b7d: Compose prints a blank line after the
  container exits. Match the line wanted (`grep '^Error'`) instead.
- A running workflow's `temporal workflow describe` has no Status line; `workflow list` has it.
- At 390 px, a long `.tag` on a command block ran under the Copy button: tags stay a few words,
  and a follow-up command gets a block of its own.
- Iterating on later batches meant re-running b1 and b2 (a new user, a few model calls) each time:
  `KEEP=1` now saves the run's user to `verify/out/user.json` (git-ignored) and `REUSE=1` signs in
  as them again.
- SearXNG asks more engines than `settings.yml` lists: `use_default_settings` keeps its defaults
  (DuckDuckGo, Brave, Wikipedia…) and the file only turns Google, Yahoo and Bing on. Read from the
  running SearXNG's `/config` (11 general engines), not from the file.

## Decision Log

- Decision: a deletion never gives up (M9, F22). Its steps keep Temporal's default,
  no schedule-to-close limit and no limit on attempts, each attempt still bounded by its
  start-to-close; a week's limit only turned a slow outage into data left behind a 202, with
  nothing to finish it. What a limit gave, a deletion that shows as failed, `make doctor` gives
  instead: it warns of any deletion running for over a day, and Temporal's UI shows the step and
  its error. Changing an activity's timeouts is safe for deletions in flight ("it is safe to
  change the input parameters, return values, and execution timeouts of Child Workflows and
  Activities"). Sources: docs.temporal.io, "Detecting Activity failures" (Schedule-To-Close
  default ∞), "Retry Policies" (maximum attempts default unlimited), "Workflow Definition" (safe
  changes); the test that failed before the fix (nine one-day retries: the workflow failed after
  seven, with only the Keycloak user disabled). The UI showing the error needed one more setting:
  Temporal 1.32.0 keeps a retrying activity's last failure only up to
  `limit.mutableStateActivityFailureSize.error` (4 KB by default; `truncateRetryableActivityFailure`
  in `service/history/workflow/mutable_state_impl.go`), and replaces a larger one with "Failure
  exceeds size limit.", dropping its encrypted attributes (`failure.Truncate`). gen9-agent's
  network error is 5.1 KB with its chain and stack traces, so gen9-temporal keeps up to 64 KB.

- Decision: apps with access (M9, F12) live in Gen9's Settings, read and revoked
  through Keycloak's admin API, not by sending people to Keycloak's account console. Settings is
  where every other sign-in matter is (password, second steps, passkeys, where you're signed in),
  in Gen9's words and theme; the console is Keycloak's look and words, and repeats those. It reads
  and revokes them as the console does, through the Account REST API with the person's own
  token, as Settings already lists and ends sessions: no admin power for a person's own matter.
  Revoking
  a consent is Keycloak's own operation, which also ends the client's offline tokens and, as the
  probe showed, its refresh token; access tokens it already holds end with their lifetime (5
  minutes), as with any sign-out. Sources: Keycloak Admin REST API (Users: consents), the probe
  (F12), GitHub's and Google's settings.

- Decision: meaning search's floor (M9, F7). Queries go to `embed` in its model's
  own form, and a match by meaning must reach a similarity floor as well as the share of the
  best, both looked up by the model the router names (`x-litellm-model-name`): Qwen3-Embedding's
  instruction for its family (its model card), and 0.40 for the 8B model, between its wrong
  matches (up to 0.419) and its right ones (from 0.437) in the probe, and above every query no
  chat answered (up to 0.334). A model not listed keeps today's behaviour, since a floor is a
  property of one model's scores (why the share was chosen); LangChain's
  `similarity_score_threshold` and LlamaIndex's `similarity_cutoff` are set per deployment the
  same way. The API learns the model from its first search and embeds again when the model
  changes; documents stay as they are (the model card: no instruction), so nothing is re-indexed.
  The reranker, off by default, remains the other tool.

- Decision: renaming a chat (M9, F5). In place in the title bar, from *Chat
  options*, rather than a dialog: the title is already there, and Claude.ai's dialog and
  ChatGPT's in-place field (support.claude.com; ChatGPT's sidebar) both save on Enter; leaving
  the field saves too, as ChatGPT's does, and Escape keeps the old name. The API takes the same
  1 to 80 characters as a first message's title and a scheduled task's name (task-form's
  `maxLength`), with whitespace collapsed, since a title is one line. A rename doesn't move the
  chat up the sidebar (`updated_at` is the last activity, and renaming isn't a conversation), and
  the first answer's title never overwrites it (the executor names only a chat still called "New
  chat"). No MCP or CLI command: the MCP server asks and reads (`ask`, `list_chats`,
  `search_chats`, `read_chat`) and manages no chat, deleting included; renaming, like deleting,
  is the person's, in the web app.

- Decision: how M9's audit is judged. Security findings against OWASP ASVS 5.0.0
  (May 2025, the current release: github.com/OWASP/ASVS/releases, 17 chapters with OAuth and
  OIDC) and the OWASP Top 10 for Agentic Applications 2026 (ASI01 goal hijack to ASI10 rogue
  agents, genai.owasp.org, 9 December 2025), which the code already cites. Each auditor is its
  own agent, run one after another, on Sonnet (AGENTS.md: subagents on a cheaper model); a
  finding counts only once reproduced against the live stacks or the code by me, and one that
  isn't is recorded as rejected, with why.
- Decision: the Reference (M7) before the deeper steps (M1 to M6). It needs no live
  flow, so it is quick to make true; with it, `reference.mjs` passes and stays a guard while the
  deeper steps are written; and each deeper step then fills in the "Seen in" links of what it
  traces. The check also grew past the plan's list, to what a reader looks up besides: the web
  app's routes, gen9-agent's settings and every settings file's keys (names only), the volumes,
  the terminal's and gen9-agent's commands, and links within the page.
- Decision: how the document is built. Sources read today:
  - **Diátaxis:** tutorials, how-to guides, reference and explanation are four kinds that must
    not blur ("the different kinds of documentation bleed into each other … it's impossible to
    meet the needs served by either", diataxis.fr/map). A tutorial keeps to what reaches the
    goal ("Ignore options and alternatives", "Ruthlessly minimise explanation") and links to
    explanation "so that it's available, but doesn't get in the way" (diataxis.fr/tutorials).
    Explanation gives "background and context … why things are so" (diataxis.fr/explanation).
    Reference describes "and only describe[s]", mirrors the product's structure and is complete
    (diataxis.fr/reference).
  - **arc42** (arc42.org/overview): twelve sections for documenting a whole system: goals,
    constraints, context and scope, solution strategy, building blocks, runtime, deployment,
    crosscutting concepts, decisions, quality, risks, glossary.
  - **C4** (c4model.com/abstractions/container): a container is "an application or a data store
    … something that needs to be running in order for the overall software system to work",
    which is what a Compose service is here.
  - **Docs as tests** (docsastests.com; Doc Detective, docs.doc-detective.com): documentation is
    run against the product, so the docs and the product are checked together. gen9-learn's
    verifier already does this for every step and command.
  - **Mayer's principles**, as gen9-learn's README already applies them: pre-training (the cast),
    segmenting (one action a step), signaling (the map), coherence.
- **So:**
  - **The core path stays a tutorial,** as today. Everything else goes in *deeper* steps, marked
    so, at the point in the story where the reader has what they need: after the step that makes
    the thing they deepen. A reader can skip every deeper step and still finish. Each is one
    action and what to notice, like any step, with its "why" in a fold-out (explanation kept
    apart), and every output verified.
  - **Orientation first** (arc42 1 to 4, C4's containers): what Gen9 is, its goals and limits, its
    context, how it's built and why.
  - **Reference last** (arc42 5, 7, 8, 9, 11, 12): the atlas and its tables, described and
    complete, each row linking to the step that traces it.
  - **Completeness is checked, not promised:** `verify/reference.mjs` reads the running system
    (Compose's services, Temporal's workflow types and Schedules, the OpenAPI document, the
    database's tables, the Makefile) and fails on anything the page doesn't name. So "nothing
    left out" holds after the next change too.
  - **Rejected:**
    - a separate reference document: the owner asked for one standalone document;
    - folding every detail into the core steps: it breaks the tutorial (Diátaxis) and a
      4-hour path would double;
    - reference tables written by hand without the check: they'd drift, as the page's code line
      numbers did (manual-e2e.md, P6-F).

## Outcomes & Retrospective

All milestones done:
- **The page:** orientation, eleven traced parts with 30 deeper steps, and a Reference.
  Every output on it came from a verifier run, and every runnable command runs.
- **The checks:** `reference.mjs` fails on anything in the running system the page doesn't name,
  across 17 kinds from services to links. Full run 5 passed all 19 batches (246 checks), for
  $0.015 of model calls.
- **What made "nothing left out" true** was the checker, not the writing. Its first run found
  the page naming 5 of 12 workflows and 2 of 34 Activities, and it kept finding things: settings,
  compose variables, routes. Written by hand, the Reference would have stopped where memory did.
- **What cost the most time:** checks that assumed one model behaviour or one ranking. Each full
  run found one (a one-word query, a truncated answer, a question asked before a tool, BM25's
  order, network idle). Each was fixed to check what the step claims, not how the model got
  there. None was a fault in Gen9.
- **Found in Gen9 itself:** a docstring naming a workflow that doesn't exist (fixed); nothing else.
- **Left for later:** the disk limit, `make audit` and `make updates` are described and pointed to
  their checks rather than run in every gen9-learn run (Decision Log, M6).

## Context

- `gen9-learn/index.html`: one self-contained page, 1,778 lines. Eleven parts (0 to
  10), then Review later, a Debugging map and Files to read. Each step is an `<li class="step">`
  with its map focus (`data-watch`, `data-focus`), one action (`.do`), a Predict fold-out, and
  what to notice by place (`.where` store, logs, devtools, code). A command sits in `.cmd` with
  `data-check="run"`, `"run-any"` or `"manual"`; a verified output in `.out` with the
  `verified` caption.
- `gen9-learn/AGENTS.md`: the rules. Every observable claim comes from a run of `verify/`; a new
  step gets its check in a batch first.
- `gen9-learn/verify/`: `run.mjs` runs the batches in order (`b1 b2 b2w b2x commands b3 b4 b5 b5x
  b6 b7`) as one throwaway user; `commands.mjs` runs every runnable command on the page;
  `page.mjs` checks the page itself (widths, themes, axe). Its last full run: 160 checks passing.
- What the page didn't cover, measured by searching it for each component: seven of
  the twelve Temporal workflows, search's machinery, environments beyond one command, connectors
  beyond a public one, background tasks, outcomes, notifications, Retry, the router's and
  Temporal's insides, the export and the privacy page, the hardening of phases 4 to 6, and the
  operator's tools (the conversation; manual-e2e.md, P6-F).
- The e2e fixtures the deeper steps can reuse: `e2e/fixtures/oauth_mcp.py`, `drift_mcp.py`,
  `apps_mcp.py`, `elicit_mcp.py`, `keycloak_mcp.py`, `git-server.mjs`. gen9-agent allows
  `host.docker.internal:17801` to `17804` for them (`CONNECTORS_ALLOWED_HOSTS`).

## Plan of work

Each milestone: write the verifier's checks for its steps first, run them, then write the steps
from what they observed; `node page.mjs`; commit. Model spend: the deeper steps ask for short
answers, as e2e does; a full run's spend is measured before and after (standing instruction 2
of manual-e2e.md) and kept under $0.10.

## Validation

- `cd gen9-learn/verify && node run.mjs` (every batch, deeper ones included) and `node
  reference.mjs` pass, and `node page.mjs` passes.
- `gen9-learn-acceptance.json`: each item flips to passing only after its check ran.

## Interfaces

- New batches in `gen9-learn/verify/batches/`, run by `run.mjs` right after the batch whose user
  and state they need; `verify/reference.mjs`, run on its own (it needs no user).
- A deeper step: `<li class="step deeper">`, with a "Deeper" label, the same parts as any step.
