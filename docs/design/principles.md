# Principles

How Gen9's screens behave, and why. Each one rests on the research (`research.md`) and says where it
shows in Gen9 today, or which item of the plan brings it. A screen design (`screens/`) names the
principles it applies. A change that breaks one needs a Decision Log entry in the active plan.

## 1. The work is visible, and it survives

A run is the unit of work. While it runs, the person sees its status, its plan and each step, live.
When it's done, the steps fold into one line ("Used 3 tools and a plan") above the answer. A reload,
a closed tab or another device shows the same run, still going or finished.
- Why: HIG (specific feedback during generation); HAX G11 (why it did what it did); products show a
  short status and the steps.
- In Gen9: runs are a durable event log on Temporal. The chat follows a run across reloads, and
  steps and plans stream in. A chat waiting for the person says "Needs you" in the sidebar; a
  status for one still running is to come (`components.md`, "To add").

## 2. The person stays in charge

Stop is always one tap. Messages sent while the agent works steer it and don't break the run.
Anything irreversible or outward-facing waits for Allow or Deny. How much the agent may do on its
own is a visible setting, not a hidden one.
- Why: HIG (keep people in control; ask before irreversible tasks); HAX G8, G9, G16, G17; Claude's
  permission modes; ChatGPT's take-over; Cursor's follow-ups.
- In Gen9: Stop cancels on the server. In a chat set to "Ask before acting", what the agent
  would change waits for Allow or Deny, showing what would really run; questions mid-task wait for
  the person's answer; the permission mode is a menu in the composer. A connector's app can't
  take a decision for them, and an admin or operator can stop what agents are doing. Steering
  with a message sent while it works is still to come (`screens/chat.md`, "Next").

## 3. Honest about limits

Say what Gen9 can do before the first question, where an answer came from, and when something
failed and why, in plain words. Never pretend. When a part is down, the rest keeps working and says
so.
- Why: HIG (set expectations, state limits); HAX G1, G2, G11.
- In Gen9: suggestions on the empty chat; sources on answers; "Gen9 is an AI system and can be
  wrong. Check its work before you rely on it.", before the first question too (AI Act
  Art. 50); the usage-limit message when over budget. Search answers by
  keyword when search by meaning is down.

## 4. Everything can be found again

Past chats are searchable by their words, by meaning and by a half-remembered title, from a search
screen and, later, by the agent itself with citations that open the chat.
- Why: HAX G12 (remember recent interactions); Claude's chat search.
- In Gen9: `GET /v1/search` and `gen9 search`. The search screen comes from this milestone;
  the agent's tool comes with memory.

## 5. Memory you can see and change

What Gen9 remembers about a person is listed where they can read, edit and delete it. It can be
paused, and a chat can be kept out of it.
- Why: HAX G12, G13, G14 (adapt cautiously), G17; HIG privacy guidance; Claude's memory controls.
- In Gen9: Settings > Memory shows what Gen9 remembers, to edit in place or clear; "Remember
  things about me" pauses it, and searching past chats has its own switch. A sensitive detail
  mentioned in passing isn't saved; one the person asks Gen9 to remember is.

## 6. Calm, precise and readable

It follows gen9-design: ink and hairlines, lapis for interaction, gold for small points only, no decorative
color. The answer is the page. Steps, sources and controls stay quiet until they're wanted. Plain
words, no jargon: a person sees "Stopped", not `cancelled`.
- Why: gen9-design's idea; HAX G5 (social norms).
- In Gen9: every screen today.

## 7. Everyone, every screen

Designed at phone width first. WCAG 2.2 AA in light and dark: 44 px touch targets, visible focus (in
forced colours too: `outline-hidden`, never `outline-none`, which a test in gen9-ui enforces),
reduced motion honoured, text at 200% on a phone without sideways scrolling or cut words (buttons
wrap their labels, long words break). To screen readers a chat is a log whose messages say who said
them ("You said", "Gen9 said"); an answer is read once it's written, not token by token (the log is
busy meanwhile), and a status says what Gen9 is doing, that it needs an answer, or that it answered.
Every page has one `h1` and a tab title that says what it is (a chat's own name); the first Tab
offers "Skip to content", past the sidebar's chats; dialogs keep focus until closed.
- Why: WCAG 2.2 (1.3.1, who said what; 1.4.4 and 1.4.10, large text; 2.4.7, focus visible; 4.1.3,
  status messages); WAI-ARIA 1.2's `log` and `aria-busy`; gen9-design's mobile-first rules.
- In Gen9: `e2e/a11y.mjs` runs axe on every screen at phone and desktop sizes, light and dark.

## 8. Tell people when something changes, not before

Work that finishes while the person is away (a scheduled task, a long run) tells them once, and
says whether it needs them. Nothing interrupts a person who is working.
- Why: HAX G3 (time services on context), G18 (notify about changes); Claude's scheduled tasks.
- In Gen9: a scheduled task's run emails its person once, when it's done or needs them, as they
  choose in Settings > Notifications, and never while they're in that chat. A background task
  tells the chat that started it when it ends.

## 9. Private by default

A person sees only their own chats, results and memory. Actions on their behalf go through their
own sign-in. Anything that leaves Gen9 (a connector, a web search) is shown as a step.
- Why: HIG privacy; HAX G16.
- In Gen9: every read is scoped to the person (search included, tested across users). Web
  searches show as steps.
