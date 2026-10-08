# Chat

Ask, watch the work, read the answer, follow up. Principles: 1 (visible and durable), 2 (in
charge), 3 (honest), 6 (calm), 7 (everyone).

## Today (built)

- **Empty chat (`/chat`):** the mark, "What should Gen9 do, <first name>?", three suggestion
  cards that fill the composer, each a different kind of task (something to find out, something
  to run, something to draft), and the composer pinned at the bottom ("Give Gen9 a task").
- **Conversation (`/chat/<id>`):**
  - A header bar with the chat's title and a menu: *Rename* turns the title into a field in place
    (Enter or leaving it saves, Escape keeps the old name; up to 80 characters, one line), and
    *Delete chat* asks first. The browser tab shows the title too (cut to 60 characters); a chat
    that isn't the person's, or is gone, is "Not found".
  - The person's messages are bubbles on the right. Gen9's answers are prose after the mark, in
    Markdown with links; a line ending is a line break, as in GitHub's comments, so an answer
    written one item per line reads that way.
  - While Gen9 works: the plan (ticks as items finish) and each step as it happens, under a label
    ("Thinking", "Searching the web"). Stop replaces Send.
  - When done: the steps fold into one line above the answer ("Used 3 tools and a plan"), which
    opens them.
  - Under the composer, from before the first question: "Gen9 is an AI system and can be wrong.
    Check its work before you rely on it." (the AI Act's Art. 50: people are told they're
    talking to an AI system at the first interaction at the latest, near the input)
- **Durability:** a reload, another tab or a return later shows the same run, still streaming or
  finished (`e2e/runs.mjs`).
- **A turn that ended without text** (stopped, or failed and not retried) says "This answer
  didn't finish." under its steps, never "Thinking", which is only for the answer being written.
- **A long message:** a message takes up to 8,000 characters. Nothing is cut off (a browser's
  `maxlength` drops the end of a long paste silently): from 90% of the limit the composer says
  how many are left, past it how many too many, in red, and Send waits until it fits (GOV.UK's
  character count).
- **Errors** are toasts in plain words: "Gen9 couldn't answer. Try again.", the usage-limit message,
  "Lost the connection to this answer. Reload the page to see it."
- **Phone:** a top bar (menu, the mark, new chat) above the title bar; the composer pinned at the
  bottom, clear of the home indicator.
- **A short screen** (under 36rem high: a phone held sideways, or 400% zoom, where a 1024-high
  screen is 256 CSS px): the top bar, the title bar and the composer scroll with the page instead
  of staying pinned, which at 400% took all 256 px (WCAG 1.4.10 Reflow). Pinned or not, focus
  scrolls clear of them (`lib/sticky-inset.ts`, 2.4.11).

## Sources

Principle 3: an answer that used the web shows where it came from, whatever the model wrote.
It's also OpenAI's rule for its web search: citations "clearly visible and clickable". ChatGPT
does it with a "Sources" button under the answer (`research.md`).

```
PostgreSQL: 18.6, released 2026-08-13 …          the answer (inline links stay as the model wrote them)
[ Sources · valkey.io, github.com +4 ]            a quiet button under the answer
```

- **The button:** under an answer whose turn searched or opened pages. It reads "Sources", then
  the first two sites and "+N" for the rest.
- **Opening it:** a sheet, from the right on desktop and from the bottom on a phone, titled
  "Sources":
  - **Cited:** the pages the answer links or cites, each with its title (when known) and its
    site. A page the answer links that none of the turn's searches or opened pages found, nor
    the provider cited, says "Not among the pages Gen9 found" (P5-C9);
  - **Also consulted:** the pages the agent opened, then those its searches returned.
  - Every entry is a link that opens in a new tab, without tracking parameters (`utm_*`, `trk`).
    No favicons: they would call other sites from the person's browser.
- **A link in an answer** whose text names another site than it goes to ("bbc.co.uk" going to
  another host), or whose host is in punycode, is followed by "(goes to <its host>)" (P5-C9).
- **The data:** each search step keeps the URLs it consulted (`action.sources`), and the answer
  keeps its citations. Both are live while answering and after a reload. Work delegated to a
  subagent ("Asked the fact checker: …") counts too: its step keeps the pages the subagent's
  searches found (P8-F2).
- **Accessibility:** a button with a visible label and a count; the sheet is a dialog with
  focus trapped and returned; links are ordinary links with their site as text.

## Questions (human in the loop)

Principle 2: when Gen9 can't go on without the person, it asks, and waits for as long as it takes
(up to 7 days). References (`research.md`):
- ChatGPT agent pauses to ask when something is unclear;
- Deep Agents Code's `ask_user` asks in text or as choices, with "Other" always possible;
- AI Elements' confirmation has a request state, then an answered state.

```
✓ Searched the web: flights to Europe in May           the steps so far
┌─────────────────────────────────────────────────┐
│ Gen9 needs your answer                          │   a card in the conversation
│ Which city should I plan for?                   │   the question, as the agent wrote it
│ ( ) Paris   ( ) Rome   ( ) Tokyo   ( ) Other …  │   choices, or a short text field
│                                  [ Send answer ]│   ink pill
└─────────────────────────────────────────────────┘
[ Answer the question above to continue    ] [Stop]   the composer waits; Stop stays
```

- **The card:** appears under the turn's steps as soon as the agent asks, live or after a reload.
  - It is titled "Gen9 needs your answer", then each question.
  - A question with choices is a radio group whose last option is "Other", which reveals a text
    field. Without choices, a question is a text field (up to 2,000 characters).
  - Several questions share one card, numbered, with one Send answer. Each question is required
    unless the agent said it is optional.
- **Sending:** Send answer shows a spinner. The answer is sent once: a second tab that answers
  later is told "Already answered" and shows the answer. The run then goes on where it was, with
  its steps and answer as usual.
- **Answered:** the card becomes a step, "Asked you: Which city should I plan for?", with "You
  answered: Rome" under it, live and after a reload. While the question waits, the step shows a
  question mark instead of a spinner: nothing is working, the person is.
- **While waiting:** the turn's label reads "Needs you", without the pulsing dot. The composer is
  disabled with the placeholder "Answer the question above to continue", and Stop still ends the
  run.
  - The Recent row in the sidebar says "Needs you", so a person who left finds the chat.
- **Ending without an answer:**
  - Stop ends the wait like any run.
  - After 7 days with no answer, the run ends, and the chat says "Gen9 stopped waiting for an
    answer."
  - Either way, the next message continues the chat, and the question is simply left unanswered.
- **Focus and accessibility:**
  - The card is announced by the conversation's live region (not busy while Gen9 waits), and the
    status says "Gen9 needs your answer."
  - If the composer had focus when it was disabled, focus moves to the card's first field.
  - Choices are a native radio group with a legend (the question).
  - Send answer stays enabled. Pressed early, it says what is missing ("Answer the question
    first.") and moves focus there. A disabled button would hide that from the keyboard and
    screen readers.
  - Touch targets are 44 px.
- **Terminal (`gen9 ask`):** prints the question and, for choices, a numbered list with "Other".
  It reads the answer from the terminal, sends it and follows the run on. An empty answer to a
  required question asks again. Ctrl-C stops the run, as always.

## Approvals and the permission mode (human in the loop)

Principle 2: whatever Gen9 does beyond the conversation, the person can have it ask first.
References (`research.md`):
- Claude in Chrome keeps a permission mode per chat, picked in the composer;
- ChatGPT agent asks before consequential actions;
- MCP asks for a human able to deny tool calls, and for their inputs to be shown first.

```
┌─────────────────────────────────────────────────┐
│ Gen9 wants to update your memory                │   the step's words, as a request
│ Add: "Favourite colour: teal"                   │   what it would change
│ [ What should Gen9 do instead? (optional)    ]  │   only after Deny is chosen
│                          [ Deny ]  [ Allow ]    │   ghost, ink pill
└─────────────────────────────────────────────────┘
[ Give Gen9 a task                 ] [Ask before acting ▾] [↑]
```

- **Permission mode:** a menu in the composer's bottom row, left of Send, under the text field.
  Each option has a line saying what it means.
  - "Ask before acting": anything that changes something waits for Allow or Deny: Gen9's memory
    of you, a command in the chat's environment, and connectors (by their own policy).
  - "Act, ask when unsure": the default. Gen9 acts, and asks a question only when it needs to.
  - The chat keeps its mode, and a new chat starts with the default. The button's label is the
    current mode, so it is always visible (principle 2: "a visible setting, not a hidden one").
- **The card:** like a question's, titled "Gen9 wants to …" with the step's words, then what
  it would change. For memory that is the text added, or replaced and what replaces it. For a
  command, "Gen9 wants to run a command in this chat's environment" and the command as typed,
  in monospace (the terminal prints it after `$ `). For a connector's tool, its arguments in
  full, however long, in a scrolling block (P5-C9).
  - **What can't be seen, shown** (P5-C9; "Trojan Source", CVE-2021-42574; Unicode's UTS #55): a
    character that is invisible or changes how text reads (bidirectional controls, zero-width
    characters, escapes, separators) shows where it is as its code, a red outlined "U+202E",
    and a line above says so ("This holds characters that are invisible or change how text
    reads…"). The terminal writes `<U+202E>` and says it too, since an escape would act on it.
  - Allow sends at once.
  - Deny first offers "What should Gen9 do instead?" (optional), then Send.
  - One card per request. A request that covers several actions lists them all, and one Allow
    or Deny decides them together. The run goes on once every request is decided.
- **After:** allowed, the card becomes the ordinary step ("Updated your memory"). Denied, it
  becomes "You declined: update your memory", with a declined mark (not the error icon), and
  your reason under it.
- **While waiting:** the same as a question: "Needs you", the composer waits, Stop stays. The
  step that waits (and anything else still marked as running) shows the question mark, not a
  spinner: nothing moves while Gen9 waits for the person. The step the card holds says so in the
  card's words, "Waiting for you: update your memory", not "Used …" for what hasn't happened.
- **Terminal:** `gen9 ask --ask-first "…"` sets the mode. An approval prints the action and
  "Allow? [y/N]"; anything but y is Deny.
- **Accessibility:** the mode menu is a labelled button ("Permission mode: Ask before acting")
  with a menu of two radio items. Allow and Deny are real buttons, and focus moves to the card
  as for a question.

## A connector asks (milestone 2)

When a connector's server needs something from the person mid-call (MCP elicitation), the run waits
as it does for a question, and a card asks. The MCP spec sets the rules: say which server asks, let
the person decline or cancel, let them review a form before sending, and never open a URL without
consent.

```
┌──────────────────────────────────────────────────────────────┐
│ travel asks (while using plan trip)                           │
│ Where to, and for how long?                                   │
│ City *      [Lisbon            ]                              │
│ Nights *    [3                 ]                              │
│ Class       [Business ▾]                                      │
│                                   Cancel  Decline  [ Send ]   │
└──────────────────────────────────────────────────────────────┘
```

- **Form:** built from the server's flat schema, in the server's order: text, email, address,
  date, number, whole number, yes or no, one choice (a select), several choices (checkboxes).
  Defaults are filled in, and required fields are marked. Gen9 checks the answers again.
- **An address:** the message, then the full URL in monospace with its host in bold. A warning
  appears when the host is punycode. "Open <host>" opens it in a new tab, with no opener, only
  when clicked. Done tells the server the person went there. The URL is never fetched by Gen9.
- **Decline and Cancel** answer every request of the round.
- **The composer** says "Answer the connector above to continue" while it waits.
- **In the terminal,** `gen9 ask` asks field by field (choices numbered, defaults in brackets),
  and opens an address in the browser only on y.

## A chat's environment (milestone 3)

A chat can run commands and keep files in an environment of its own (an OpenSandbox sandbox),
created the first time Gen9 needs one in that chat. Nothing in the chat asks for it: the steps show
what happened.

- **Steps:** a command reads "Ran: <command>" (its first 120 characters); files read "Read …",
  "Wrote …", "Edited …" as before. They fold with the others once the answer is written.
- **Ask before acting:** a command waits for Allow or Deny, shown as typed (above).
- **Its life:** it lasts while the chat uses it and 30 minutes after; then a later command starts
  a new, empty one. Deleting the chat removes it.
- **Files it shares:** what Gen9 saves for you in the environment's out folder is listed under the
  answer of that turn, each a button with its name and size that downloads it (never opens it:
  code made it). They stay after the environment is gone, until the chat is deleted. In the
  terminal, `gen9 ask` lists them at the end, and `gen9 files <chat> <name>` downloads one.
- **Files you attach:** the paperclip left of the permission mode picks files (several at once);
  each shows as a chip under the text with its name, size and a remove button, a spinner while
  it uploads, or why it was refused (over 25 MB). Send waits until they're attached. The sent
  question shows "Attached: …", and Gen9 finds them in the environment's `/work/in`. In the
  terminal: `gen9 ask --attach FILE` (again for more).

## A connector's app (milestone 2)

A connector tool can come with a View (MCP Apps): the server's own HTML, shown under the step that
used it, below the steps (which fold once the answer is written), so it stays in sight.

```
┌──────────────────────────────────────────────────────────────┐
│ App from board, not made by Gen9                              │
├──────────────────────────────────────────────────────────────┤
│  (the server's View, in its sandbox)                          │
├──────────────────────────────────────────────────────────────┤
│ board’s app wants to use move.                [Allow] [Deny]  │
└──────────────────────────────────────────────────────────────┘
```

- **Its boundary:** a bordered frame captioned with the connector's name and "not made by Gen9",
  as the spec asks hosts to mark sandboxed UI. The frame's title names it for screen readers. Its
  height follows the View, between 120 and 900 px.
- **Where it runs:** on the sandbox's origin for that connector, never Gen9's, under a CSP of the
  domains it declared. It can't reach the page around it.
- **What it asks for** shows as a bar under it:
  - a tool of its server: "Allow" or "Deny" when the connector's policy asks (as for the model's
    calls); without a question otherwise;
  - a link: the full address with its host in bold, the punycode warning as for a connector's
    address, and "Open <host>" (a new tab, no opener) or Cancel;
  - a message: put in the composer for the person to send, never sent by the View, with "From
    <connector>'s app, not written by you. Read it before you send it." under it until they change
    it. Over a draft of theirs only after "Replace mine" (or "Keep mine") on the bar, which shows
    the message.
- **Its asks can't take a decision the person didn't make** (P5-C1). A View can ask at any moment,
  not only when clicked:
  - the bar takes the focus, onto itself and not its first button, only when the person is in that
    View; anywhere else (the composer) the focus and their keys stay put, and screen readers hear
    that the app is asking;
  - "Allow", "Open" and "Replace mine" act only once the bar has been where it is for 500 ms (the
    View resizing moves it, and starts that again), as Chromium's permission prompts ignore a click
    within the double-click interval;
  - a new ask ends the one before, unanswered;
  - a message that lands in the composer moves the focus there only from the View, and Send does
    nothing for 500 ms after it lands.
- **After a reload** it renders again from the saved step, with the same result.
- **In the terminal** there is no View: `gen9 ask` shows the tool's text, which the spec asks
  servers to send too.

## Retry (human in the loop)

Principle 3 (honest about limits) and 2 (in charge): when Gen9 fails in a way that can be fixed
from outside, the answer waits for the person to try again. The work done so far is kept.

```
✓ Searched the web: …                                  the steps so far stay
┌─────────────────────────────────────────────────┐
│ Gen9 couldn't finish                            │
│ The model provider isn't answering right now.   │   the reason, in plain words
│                                     [ Retry ]   │   ink pill
└─────────────────────────────────────────────────┘
[ Retry above, or stop this answer        ] [Stop]
```

- **When:** the turn's retries ran out on something outside (the provider down or out of
  credits, the connection lost), or the person is over their usage limit, which says when it
  resets ("…It resets on 1 October 2026 at 00:00 UTC: try again then, or ask an admin."; P5-D1). A failure a retry can't
  fix still ends with the toast "Gen9 couldn't answer. Try again."
- **Retry** continues the same answer: the question isn't sent twice, and the steps already done
  aren't done again. Whatever part of the answer was written is cleared when it starts again.
- **Meanwhile:** "Needs you", the composer waits, and Stop gives up. After 7 days without a Retry
  the answer ends with the error.
- **Terminal:** `gen9 ask` prints the reason and "Retry? [Y/n]".

## A graded answer (milestone 5)

A scheduled task with a rubric has each answer graded by a separate grader (scheduled.md, "Done
when"). Under the answer it graded, after Sources:
- one line, folded: "Meets its rubric · 3 of 3", "Short of its rubric · 1 of 2 met", or "Its
  rubric doesn't apply to what was asked", with "(try 2)" after the first try;
- opened (a native disclosure, so the keyboard and screen readers know it): the grader's
  explanation, then each criterion as "Met:" or "Not met:" in words (not only colour), and why.

The next try's message is the grader's findings, shown as the chat's next question, so the
revision is visible and in order.

## In the background (milestone 5)

Gen9 can start work that goes on while the conversation does (gen9-agent's background.py).
Principles 1 (the work is visible) and 2 (in charge).
- The step says "Started in the background: <what>", with "Open its chat" under it (inside the
  folded steps once the turn ends; the list below stays in view).
- Above the composer, "In the background" lists the chat's tasks, each a link to its chat with
  its state in the chat's words: "Working" (with the leaf dot), "Needs you" (bold), "Done",
  "Didn't finish", "Stopped". It refreshes itself while one is unfinished.
- A task's chat shows its work as any chat does, with "A background task. It takes its
  instructions from the chat that started it." in place of the composer, and Stop while it works.
  It isn't in the sidebar's list.
- The other steps say "Checked a background task", "Gave a background task new instructions",
  "Stopped a background task" and "Looked at the background tasks".
- A task that needs the person (Allow or Deny, a question, a connector's form, Retry) says
  "Needs you" in the list, with the same card the chat uses under it. It's answered there or in
  the task's own chat, and the first answer counts. The sidebar says "Needs you" on the chat.
- When a task ends, the chat gets a turn of its own (principle 8: tell people when something
  changes). Its question is a notice, not the person's bubble: a bordered note headed "From a
  background task", showing the task's title and the start of its answer (three lines). Then
  Gen9's answer, which says what the task found. An open chat picks it up and follows it
  without a reload. A chat in the middle of an answer gets it after.

## Past chats

When Gen9 looks through the person's earlier chats, the step says "Searched your past chats:
<query>" or "Looked at your recent chats". The chats it used are listed under Sources, as a web
search's pages, each opening that chat. Settings > Memory turns it off.

## Next (planned, with their items)

- **Stopped answers:** one stopped before any text says "This answer didn't finish." (built,
  above). Whether one stopped mid-text should be marked "Stopped" is still to decide.
- **Steer while working:** a message sent during a run is queued and delivered at the agent's next
  step, not as a new run (human in the loop; Cursor's pattern, `research.md`).
- **Titles:** generated from the first exchange instead of the first message (small, with memory
  or before).
