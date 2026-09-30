# Release: a mark and a home page that say what Gen9 is

## Purpose

Someone who opens a Gen9 installation for the first time understands what it is before signing in:
a general-purpose agent their organization runs on its own servers and shapes to its own work. The
home page says so in one screen, the mark is Gen9's own, and the words inside the app agree with
both. Anyone can then clone the repository, run `make setup && make up` on Linux or macOS, and see
every check pass.

How to see it working: open http://localhost:14000 signed out (the headline, one example task, two
actions); sign in and read the empty chat and the composer; look at the browser tab's icon and the
sign-in page's wordmark; run `make e2e` on a Linux machine without setting anything first.

Asked by the owner: "gen9 home page text is not correct, its not what the product is, its a general
purpose harness that runs on servers and can be customized to anything … i don't wanna call it
harness either thats a technical word, but also wanna properly represent it". On the mock: "its too
long, just say something along the line achieve ANY task through autonomous agents, configure your
own agents, then that picture on the side, then something to call for action there, give it
something to do". Then: "looks good logo and everything".

## Progress

- [x] Research and plan: Decision Log, "What to call it", "The home page", "The mark".
- [x] The checks read settings keys and command names that hold a digit (Surprises, "A digit in the
  name"). Verified: the four seeded-user keys read from `gen9-keycloak/.env`; `reference.mjs` counts
  gen9-agent's 6 commands.
- [x] M1 The mark in gen9-design, copied into the apps.
  - [x] `scripts/wordmark.py`, `brand.py`, `raster.py`; `brand/` rebuilt; `react/logo.tsx`.
  - [x] gen9-design's README and `theme.css` say how the mark and gold are used.
  - [x] `make design-sync`; the guide's own mark; the three places a wordmark is sized (the
    home page `h-8`, the sidebar and the sign-in pages `h-7`).
  - [x] Checks: gen9-ui (tsc, eslint, 154 tests), the Keycloak theme (tsc), `page.mjs`,
    `reference.mjs`, `make design-check`.
  - [x] Live in Chrome after `make up STACKS="keycloak ui"` (85 s): the sidebar's wordmark and the
    empty chat's mark, a Keycloak page's wordmark, `/brand/icon-192.png`.
- [x] M1b The emails carry the mark too (Surprises, "The emails kept the earlier wordmark"): the
  header of `gen9-keycloak/theme/src/email/html/template.ftl`, set in type. Live after
  `make up STACKS=keycloak` (24 s): `recovery.mjs` sent a reset email, and the one in Mailpit opens
  with `[9] gen9`, the 9 in lapis, and no gold point.
- [x] M2 The home page.
  - [x] `docs/design/screens/home.md`, then `gen9-ui/app/page.tsx` built to it.
  - [x] The page's and the manifest's descriptions.
  - [x] `e2e/a11y.mjs` covers it as before; `e2e/stacks.mjs` checks its words and its two actions.
  - [x] Live after `make up STACKS=ui` (20 s): at 1440 by 900 nothing scrolls; `stacks.mjs` and
    `a11y.mjs` pass (every screen, phone and desktop, light and dark). The approval's "With" shows
    the arguments as JSON, as the chat prints them; the mock had them as two plain lines.
- [x] M3 The words inside the app.
  - [x] The empty chat ("What should Gen9 do, Ada?"), the composer ("Give Gen9 a task"), the three
    suggestions (something to find out, to run, to draft).
  - [x] The notice under the composer and in the terminal ("… Check its work before you rely on
    it."); the privacy page's first sentence.
  - [x] The agent's definition (`agents/gen9/AGENTS.md`): "a precise general-purpose agent"; every
    behaviour stays.
  - [x] Docs that quote them: `docs/design`, `docs/ai-act.md`, gen9-learn's two notices (copied
    from what Chrome and `gen9 login` showed).
  - [x] Live after `make up STACKS="agent ui"` (48 s): the empty chat, the composer and the notice
    in Chrome; `/privacy`; `gen9 login` by device flow, confirmed in Chrome, prints the notice;
    `gen9 whoami`.
  - [x] gen9-learn's `b1d` observed the notice under the composer word for word; the checks that
    rest on the agent's behaviour pass with the new definition: `runs.mjs` 8 of 8, `questions.mjs`
    14 of 14, `approvals.mjs` 16 of 16, and `tests/test_definition.py`.
- [x] M4 Every check runs on Linux as it does on macOS: Chrome's path. `e2e/browser.mjs` and
  `gen9-learn/verify/lib.mjs` hold the one default per platform; 41 scripts lost their own copy.
  Verified on Linux with `CHROME_PATH` unset: `a11y.mjs` (every screen) and `token.mjs` (the
  terminal's sign-in, confirmed in headless Chrome) pass. `make e2e` as a whole: M5.
- [ ] M5 The whole system verified on a fresh Linux machine (Validation).
  - [x] The 46 checks of `make e2e`, each by itself, `CHROME_PATH` unset: 43 passed, 3 failed for the
    checks' own reasons (Surprises), fixed, and pass.
  - [x] gen9-learn's `node run.mjs`, in full: 253 steps pass; 2 hold only on the shipped settings
    (Surprises).
  - [x] `make evals TRIALS=1`: 14 of 15 tasks; `past-chat-cited` missed once and passed 4 of 4 when
    rerun (Surprises). `make evals SUITE=canary`: fails, as it is made to.
  - [x] gen9-cli against the running stacks: `login` (device flow), `whoami`, `ask`, `search`,
    `tasks`, `logout`.
  - [x] `make updates`: 42 image references looked up; it lists 1 rebuilt tag and 15 newer releases,
    and exits 1 because 4 lookups at `docker.langfuse.com` gave no digest.
  - [x] `make stop-agents` and `make resume-agents`: the worker stops and comes back, the API and
    the web app answer meanwhile, both in the audit record.
  - [x] `make down`, `make up`: the same chats, runs, spend rows and emails after as before.
  - [x] `make backup` and `make restore` (Surprises, "A backup folder Docker can't mount"): 13
    volumes and 19 settings files, 128 MB, 126 s; restored in 107 s, the chat made after the backup
    gone and the chat deleted after it deleted again.
  - [x] `make wipe` then `make up` (empty data, the seeded users again, the settings kept);
    `make distclean` (every settings file gone, no tracked file changed); `make fresh` with the
    provider key in its environment (162 s).
  - [ ] On the fresh install: `make e2e` as one command.
- [x] M6 The licence and notices: `LICENSE` (Apache-2.0, the text as apache.org publishes it), `NOTICE`
  (what the repository carries from other projects, each with its licence and where its text is),
  and the README's first paragraph and "Licence" section.
- [ ] M7 The public repository, protected and checked (Decision Log, "The public repository").
  - [x] Settings, through `gh`, each read back: a ruleset on `main` (no deletion, no force push,
    linear history, changes only through a pull request, squash merging) and one on `v*` tags
    (no deletion, no move); Dependabot alerts and security updates; private vulnerability
    reporting; CodeQL default setup (its first run passed); immutable releases; Actions pinned
    to full commit SHAs, workflows from outside contributors only after approval, the token
    read-only; no wiki or projects; squash merges, branches deleted after merge.
  - [x] CI started by hand on `main` (the workflow as it was, `workflow_dispatch`): see the pull
    request that turns it on for the result.
  - [ ] `SECURITY.md`, `.github/dependabot.yml`, CI on every pull request and on `main`; then the
    ruleset requires its jobs.
- [ ] M8 The README and the docs, for people and for any AI agent (Decision Log, "The README").
  - [x] `README.md`: what Gen9 is and does, a quick start, how it is built, where to read more, and
    where agents start; everything else it held moved, word for word, to `docs/operations.md`
    (requirements, setup, everyday commands, upgrade, backup, stopping the agents, starting over,
    disk) and `docs/development.md` (working on Gen9, checks, how stacks stay decoupled, adding a
    stack). The long list of what `make e2e` checks became a summary and a link: each item is in
    `e2e/README.md`, checked one by one.
  - [x] `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md` (Contributor Covenant 3.0), issue forms (bug, feature,
    question; security and the guide as links), a pull request template.
  - [x] Every reference to a moved section points at its new place, as a link where the file
    renders Markdown; AGENTS.md's map and its "keep the top level in sync" rule follow.
  - [x] Checked: every relative link and anchor in the Markdown docs resolves (lychee 0.24.2,
    offline: 154 checked, 0 errors); gen9-learn's `page.mjs` and `reference.mjs` pass on the
    changed page. The pages as GitHub renders them: on the pull request.

## Surprises & Discoveries

- **A digit in the name.** Every check read settings files with `/^[A-Z_]+=/`, which drops a key
  that holds a digit. Of `gen9-keycloak/.env`'s 18 keys it read 7, and none of the four
  `GEN9_SEED_*` keys, so each check would have failed at its first sign-in. `reference.mjs` matched
  gen9-agent's commands with `[a-z-]+` and reported "gen9-agent commands: 0, each named on the page":
  a check that passes on nothing. Both patterns now allow digits. Evidence: `node reference.mjs`
  prints "gen9-agent commands: 6".
- **An alias with no price is outside every budget.** With `chat` pointed straight at OpenAI
  (`openai/gpt-6-luna`), LiteLLM 1.102 had no price for the model: five calls, 36,815 tokens in and
  196 out, were logged at $0.000000, so neither a person's limit nor the worker's daily limit
  counted them. `input_cost_per_token` and `output_cost_per_token` on the alias fix it, as
  `config.yaml` already does for `vision` and `embed`. Evidence: `LiteLLM_SpendLogs` before and after.
- **The router reads `config.yaml` only at start.** `make up STACKS=models` leaves a running router
  as it is after the file changes ("Up 45 minutes"): `docker restart gen9-models-litellm-1`.
- **Simple marks are all taken.** A reverse image search (Google Lens) of six candidate marks found
  close look-alikes for five: a 9 with a point in its bowl, a many-sided G with a dot, a cat's
  head, crop marks round a point, a nine-sided ring. Only the 9 in square brackets came back with
  nothing but plain number-nine icons. A clean search is not a trademark clearance.
- **First run on amd64.** The stacks built and came up healthy on Linux/amd64 (Docker 29.8,
  Compose 5.5) with no change to a Dockerfile. Two builds stalled on downloads inside Docker
  Desktop's build network while large layers were pulling (`DeadlineExceeded` on a release asset;
  `apt-get update` silent for eight minutes); the same downloads alone took under two seconds, and
  a rerun passed.
- **The checks' Chrome is a Mac's.** Each check defaults to
  `/Applications/Google Chrome.app/…`; on Linux every one fails to launch until `CHROME_PATH` is set.
- **The emails kept the earlier wordmark.** Reading what the full run sent (Mailpit, 55 messages):
  Keycloak's three emails (verify, reset, update your account) still opened with `gen9` and a gold
  point, the wordmark before `[9]`. `make design-check` compares the apps' copies of the tokens,
  font and logo component; the email template draws its own header in inline HTML, so nothing
  compared it. The mark can't be the drawing there: Gmail shows neither an inline `<svg>` nor a
  `data:` image ([caniemail, html-svg](https://www.caniemail.com/features/html-svg/),
  [image-base64](https://www.caniemail.com/features/image-base64/)), and a linked image needs an
  address the reader's mail client can reach. So it is type: `[9]` in bold, the 9 in lapis, then
  `gen9`. gen9-agent's task emails are plain text and name Gen9 in words.
- **Two of the guide's steps hold only on the shipped settings.** gen9-learn's full `node run.mjs`
  passed 253 steps and failed 2, both from this machine's stand-ins and not from the repository.
  "Meaning: a question no chat is about finds none" found 1 chat: search keeps matches above a
  floor only for the shipped embedding model (`SIMILARITY_FLOORS` in gen9-agent's `api/search.py`),
  and the stand-in, OpenAI's `text-embedding-3-small`, has none. "What it may spend" read $2 and
  $0.50 a day where the page says $5 and $5: the two caps were lowered in `gen9-models/.env` for
  the run.
- **A backup folder Docker can't mount.** `make backup DIR=<a folder under /tmp>` stopped all 26
  containers, then failed at the first volume: "mounts denied: The path … is not shared from the
  host and is not known to Docker" (Docker Desktop shares only some host paths; on this machine
  the home folder, not `/tmp`). The stacks came back, after two minutes down for nothing. Worse
  the other way: `make restore` wipes the stacks before it reads the backup's volumes, so a
  backup moved to such a folder would have left them empty. Both scripts now try the mount first
  and change nothing if Docker refuses it. Evidence: the backup into `/tmp/…` exits 1 with 26
  containers still running and no folder left; the restore from a copy under `/tmp/…` exits 1
  with the chats as they were; the same backup and restore under the home folder succeed.
- **One eval task missed once in five trials.** `past-chat-cited` answered "Marram" without its
  suffix in the full suite's single trial, and passed 4 of 4 when rerun by itself.
  `e2e/past-chats.mjs` notes the same miss and uses letters only for its suffix ("a hex suffix can
  read as a number"); `evals/run.py` still makes a hex one. Left as it is: one or two runs can't
  show a change there works.
- **Starting the worker sweeps accounts deleted in Keycloak.** After `make down` and `make up`
  the app's `users` went from 4 to 2: two `account.sweep` records, for the throwaway users the
  checks had deleted in Keycloak. Housekeeping, not loss: chats, runs and spend were unchanged.
- **`make e2e` right after `make fresh` failed in `search.mjs`.** Eleven minutes after a fresh
  install, "semantic's query can use its model's HNSW index" failed: the plan had no such index,
  because none existed yet. A model's index is built by `ReindexSearchWorkflow`, which the
  `reindex-search` Schedule starts every 15 minutes (`search_reindex_interval_s`); until its first
  firing an install's rows are searched exactly, which every other step of the check showed
  working. The check asked for the index before the step where it triggers the reindex itself.
  It asks after it now. Evidence: with the index dropped first, as on a new install, the check
  passes; the first full run had passed only because that install was hours old.
- **`make e2e` on a second new install failed in `directory.mjs`.** Fourteen checks passed in a
  row (`search.mjs` among them, four minutes after the install), then "github" found a server of
  Smithery's first. The directory is Gen9's copy of the MCP Registry, and its first pass was
  still under way eighteen minutes after the install (about 540 servers a minute; 5,018 when the
  check searched, over 14,000 later): it had not yet reached `io.github.github/github-mcp-server`.
  The check waited for Cloudflare's server only. It waits for both servers it searches for now.
  Evidence: once the pass had reached GitHub's server, the check passed all six steps, the pass
  still running.
- **Three checks deleted their chat by the menu's first item.** `runs.mjs`, `models.mjs` and
  `plugins.mjs` clicked the first `[role=menuitem]` under "Chat options". Since the menu has
  "Rename" above "Delete chat", that opened the rename field, no dialog came, and the chat stayed:
  "FAIL the chat and its runs are deleted" in `runs.mjs`, "FAIL the chat is deleted" in `models.mjs`,
  with every step before it passing. They now pick the item named "Delete chat", as `stacks.mjs` does.
  Evidence: `runs.mjs` 8 of 8, `models.mjs` 9 of 9, `plugins.mjs` 52 of 52.
- **A toast scanned while it fades in fails contrast.** `notifications.mjs` ran axe the moment the
  choice was saved, which is when "Saved." appears. A probe scanned at six delays after the toast
  showed: at 50 ms its text measured 1.33:1 (the toast at 80% opacity), and at 0, 150, 300, 600 and
  2000 ms nothing failed, as before any click. On this machine the scan landed inside that window.
  The check now waits for the toast to be fully shown, then scans. The toast at rest passes.
- **GPT-6 Luna cannot stand in for `vision`.** With only OpenAI's key on this machine, `vision` was
  pointed at GPT-6 Luna for the run, and `models.mjs` failed: the red square came back "Blue" and
  "Gray" (4 of 4), as gen9-models' README records for flat colour. GPT-5.4 nano read it right
  ("Red", and "green" for a green one), and with it the check passes. The committed `vision` model
  (Ling 3.0 Flash VL, on OpenRouter) was not called here: it needs an OpenRouter key.

## Decision Log

- Decision: on the page Gen9 is "agents" that get a task done, never a "harness". Rationale:
  "harness" is the builders' word (LangChain defines it for Deep Agents, which Gen9 is built on:
  "opinionated, batteries-included frameworks with built-in tools and capabilities for building
  sophisticated, long-running agents"); a person about to sign in has no use for it. "Platform" is
  what comparable projects call themselves to the people who host them (Open WebUI "self-hosted AI
  platform", LibreChat "The Open-Source AI Platform", Dify, OpenClaw Enterprise "The Open Agent
  Platform"): right for the README. "Assistant" (AnythingLLM, OpenClaw) undersells a system that
  plans, runs code and works unattended. Sources:
  [LangChain, frameworks, runtimes and harnesses](https://docs.langchain.com/oss/python/concepts/products),
  the home pages of [Open WebUI](https://openwebui.com/), [LibreChat](https://www.librechat.ai/),
  [AnythingLLM](https://anythingllm.com/), [Dify](https://dify.ai/), [OpenClaw](https://openclaw.ai/).
- Decision: the home page is one screen: the headline "Get any task done with autonomous agents.",
  one sentence on configuring your own, "Give it something to do." over the two actions, and one
  example of a task beside them. Rationale: the owner's direction after a longer version ("its too
  long"); a home page must say what the thing is at a glance and show a real example, with specific
  actions ([Nielsen Norman Group, homepage design principles](https://www.nngroup.com/articles/homepage-design-principles/));
  developer tools' pages that work keep to a headline, the product itself as the picture and two
  actions ([Evil Martians, 100 dev tool landing pages](https://evilmartians.com/chronicles/we-studied-100-devtool-landing-pages-here-is-what-actually-works-in-2025)).
  The picture stays beside the text, as the page had it and as the owner asked, although that
  study found the centred layout more common: this is an installation's front door with a sign-in,
  not a marketing page.
- Decision: the example shows a task, not a question: a plan ticked off, three steps, an answer, and
  an approval waiting. Rationale: it is the product's own screen (principles 1 and 2: the work is
  visible, the person stays in charge), in the app's own words ("Used 3 tools and a plan", "Ran: …",
  "Gen9 wants to use Slack: post message").
- Decision: no AI notice and no privacy link on the home page. Rationale: the owner's direction. The
  notice the AI Act asks for is given where a person meets the AI: on the sign-up page and under
  the composer before the first question (`docs/ai-act.md`); `/privacy` stays linked from every
  sign-in page and from Settings.
- Decision: the mark is the 9 in square brackets, ink brackets and a lapis 9; white brackets and a
  gold 9 on the lapis app icon. The wordmark is the mark followed by "gen9". Rationale: brackets mark
  the part you fill in, and that is the product, a general agent with a slot for your work; it
  reads at 16 px, so one drawing serves every size; it is the only candidate without a look-alike
  (Surprises). The maskable icon keeps the mark inside the circle of 40% radius
  ([web.dev, maskable icons](https://web.dev/articles/maskable-icon)). Gold no longer sits in the
  mark on a surface: it stays for small points (the 9 on the app icon, the dot beside work under
  way), never for text.
- Decision: the words inside the app follow the home page: the empty chat asks what Gen9 should do,
  the composer says "Give Gen9 a task", the suggestions are three different kinds of task, and the
  notice says "Check its work before you rely on it." Rationale: an answer without sources (a file
  written, a ticket opened) has nothing to "open"; the first sentence, "Gen9 is an AI system and can
  be wrong.", is the disclosure and does not change.
- Decision: the agent Gen9 ships with stays the one folder, described as a general agent; research
  is one of its skills. Its rules of behaviour (search when facts change, cite, plan, ask, treat what
  it reads as information) are unchanged. Rationale: the checks and the evals rest on that behaviour.
- Decision: each installation does not yet set its own home page text. Rationale: the headline is
  true of any installation; a setting nobody has asked for is one more thing to document and test.
  Revisit when an operator needs it: the agent's definition (`AGENTS.md`) is where it would come from.
- Decision: plans and docs carry no dates; git history dates them. `docs/PLANS.md` and
  `docs/design/README.md` say so. Rationale: the owner's direction for the published tree.
- Decision: Chrome's default path depends on the platform (`/Applications/…` on macOS,
  `/usr/bin/google-chrome` elsewhere); `CHROME_PATH` still overrides it. Rationale: `make e2e`
  should run on Linux as cloned.
- Decision: Apache-2.0. Rationale: permissive, with an express patent grant that MIT lacks; it is
  the licence of most of what Gen9 builds on (LangChain, Deep Agents, Keycloak, Temporal's SDKs).
  The owner can change it before publishing: it is one file.

- Decision: the public repository's protection is what GitHub offers a public repository on a
  personal account, and all of it: a ruleset on the default branch and on release tags (rulesets
  are available "in public repositories with GitHub Free", GitHub's docs source,
  `data/reusables/gated-features/repo-rules.md`), secret scanning with push protection,
  Dependabot alerts and security updates, CodeQL default setup, private vulnerability reporting
  ([quickstart for securing your repository](https://docs.github.com/en/code-security/getting-started/quickstart-for-securing-your-repository)),
  immutable releases ([generally available](https://github.blog/changelog/2025-10-28-immutable-releases-are-now-generally-available/)).
  Generic secret patterns and validity checks stay off: they need an organization with Secret
  Protection (`secret-scanning-non-provider-patterns.md`, same source). Rationale: the owner's
  direction ("first protect the main branch, then do the rest"); every change reaches `main`
  through a pull request.
- Decision: CI runs on every pull request and on `main` again. Rationale: GitHub-hosted standard
  runners stay free for public repositories under the 2026 pricing
  ([pricing changes for GitHub Actions](https://github.com/resources/insights/2026-pricing-changes-for-github-actions)),
  which removes the reason it was paused. The workflow already follows GitHub's hardening:
  actions pinned by SHA, a read-only token, no `pull_request_target`, `persist-credentials: false`;
  zizmor 1.30.1 finds nothing in it.
- Decision: Dependabot version updates cover the Actions only, grouped weekly, each release
  proposed after 7 days (`cooldown`,
  [Dependabot options reference](https://docs.github.com/en/code-security/dependabot/working-with-dependabot/dependabot-options-reference)).
  Images stay with `make updates`, and vulnerable packages come as security updates. Rationale:
  one mechanism per kind of dependency, and few pull requests for one maintainer.
- Decision: `SECURITY.md` follows the OpenSSF template for GitHub's private reporting
  (`ossf/oss-vulnerability-guide`, `templates/security_policies/github_security_policy.md`):
  where to report, when to expect an answer, a 90-day disclosure. It links to the documents
  that describe Gen9's security rather than repeating them. The owner can shorten or lengthen
  the 7 days it promises for an acknowledgement.

- Decision: the README is for people, AGENTS.md for agents, and both lead to the same documents.
  The README answers what the project does, why it is useful, how to start, where to get help and
  who maintains it ([GitHub, "About READMEs"](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-readmes);
  [Open Source Guides, "Starting an open source project"](https://opensource.guide/starting-a-project/)),
  and sends coding agents to AGENTS.md, which most of them read by themselves and which the Linux
  Foundation's Agentic AI Foundation now stewards ([agents.md](https://agents.md/)). The operating
  and developing detail moved to `docs/`, not away: each fact stays in one place and the rest
  link to it (the owner's direction). No `llms.txt`: it is a convention for websites, and no major
  model provider has said its systems read one.
- Decision: the community files GitHub's community profile asks for (it scored the repository
  42%): a contributing guide, a code of conduct (Contributor Covenant 3.0, from
  `EthicalSource/contributor_covenant`), issue forms and a pull request template
  ([configuring issue templates](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/configuring-issue-templates-for-your-repository)).
  Conduct reports go to the maintainer by email: GitHub's reporting to maintainers exists only
  for repositories an organization owns.

## Outcomes & Retrospective

(at each milestone)

## Context

- The home page is `gen9-ui/app/page.tsx`; signed in, `/` redirects to `/chat`. Its description
  is in `app/layout.tsx` and `app/manifest.ts`.
- The mark, the wordmark and the icons are built in `gen9-design` (`scripts/`, `brand/`,
  `react/logo.tsx`) and copied into gen9-ui and gen9-keycloak's theme by `make design-sync`;
  `make design-check` fails when a copy differs. gen9-learn's page draws the mark inline.
- The empty chat, the composer and the notice are in `gen9-ui/components/chat/chat-view.tsx`; the
  terminal's notice in `gen9-cli/src/gen9_cli/main.py`; the agent's definition in
  `gen9-agent/src/gen9_agent/agents/gen9/AGENTS.md`.
- The page was approved as a mock built from the design system itself (`theme.css` through the
  app's Tailwind) and then ported; the mock is gone, the page is the source.

## Plan of work

M1 puts the mark where every surface takes it from. M2 designs the home page in
`docs/design/screens/home.md`, then builds it. M3 brings the app's words into line and re-observes
what gen9-learn quotes. M4 makes the checks start on Linux. M5 is the proof: the whole system on a
fresh machine. M6 adds the licence.

## Validation

On a machine that has never run Gen9 (Linux/amd64 here):

    make doctor && make setup && make config && make up && make ps
    make e2e
    cd gen9-learn/verify && node page.mjs && node reference.mjs && node run.mjs
    make audit && make design-check
    make down && make up            # data kept
    make backup DIR=… && make restore DIR=…
    make stop-agents && make resume-agents
    make wipe && make up            # a new install's data
    make fresh                      # a new install, one confirmation

Spend through the router is read before and after (`docs/plans/manual-e2e.md`, Validation).

## Interfaces

- `gen9-design/react/logo.tsx`: `Mark` (the brackets in `currentColor`, the 9 in `--link`;
  `decorative` hides it from assistive technology) and `Wordmark` (the mark, then "gen9").
- `gen9-design/brand/`: `mark.svg`, `wordmark.svg`, their `-on-dark` versions, `app-icon.svg`,
  `app-icon-maskable.svg`, `favicon.ico`, `png/`.
