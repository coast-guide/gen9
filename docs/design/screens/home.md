# Home

What Gen9 is, in one screen, before anyone signs in: agents that get a task done, that a team
configures for itself. Principles: 3 (honest about what it can do), 1 and 2 (the example shows the
work and who stays in charge), 6 (calm, precise), 7 (everyone, every screen).

Built: `gen9-ui/app/page.tsx`, checked by `e2e/stacks.mjs` (its words and its two actions) and by
`e2e/a11y.mjs`. Designed and decided in `docs/plans/release.md` (Decision Log, "the home page").

## Where

- **Route:** `/`, signed out. Signed in, `/` goes to `/chat`.
- **Reached from:** the address itself, "Go to the home page" on the signed-out, not-found and
  sign-in-problem pages, and the wordmark.

## Layout

```
[9] gen9                                                        (wordmark)

Get any task done                     ┌ ─                                  ─ ┐  (crop marks)
with autonomous agents.                 ┌──────────────────────────────────┐
                                        │        (the person's request) ▢  │
Configure your own agents: what         │ [9] ▾ Used 3 tools and a plan    │
they know, the tools they use, and      │     ✓ plan, ticked off           │
what they may do without asking.        │     ✓ steps                      │
                                        │     the answer                   │
Give it something to do.                │     ┌ Gen9 wants to use … ─────┐ │
( Sign in ) ( Create an account )       │     │ With …      Deny  Allow  │ │
                                        │     └──────────────────────────┘ │
                                        └──────────────────────────────────┘
                                      └ ─                                  ─ ┘
```

- **Desktop (`lg` and up):** two columns of twelve, five for the words and seven for the example,
  centred in the height of the window. At 1440 by 900 nothing scrolls.
- **Phone and tablet:** the words, then the example, in one column. The two actions are pinned to
  the bottom with "Give it something to do." above them, in the thumb's reach and above the home
  indicator; the page scrolls under them.
- **Nothing else:** no navigation, no sections below, no footer. The page has one job.

## Words

- **Headline:** "Get any task done with autonomous agents." It names what the thing is and what it
  is for. Not "harness", not "platform": those are the builders' and the hosts' words.
- **Under it:** "Configure your own agents: what they know, the tools they use, and what they may
  do without asking." The three things an installation sets: skills, connectors, permission.
- **Above the actions:** "Give it something to do."
- **Actions:** "Sign in" (primary) and "Create an account", to `/auth/login` and
  `/auth/login?intent=signup`.
- **Not here:** the AI notice and the privacy link. A person reads the notice on the sign-up page
  and under the composer before the first question, and `/privacy` from every sign-in page and
  from Settings (`docs/ai-act.md`).

## The example

One task as the chat shows it, not a question and its answer, because a task is what sets Gen9
apart from a chat window. It is set like the rest of the app, a card on the canvas, with crop marks
at its corners (from `sm` up).

- The request, in the person's bubble.
- "Used 3 tools and a plan", open: the plan with each item done, then the steps in the app's own
  words ("Read /work/…", "Ran: …", "Used Sheets: read range").
- The answer, two lines.
- An approval waiting: "Gen9 wants to use Slack: post message", what it would send, Deny and Allow.
  The buttons are drawn, not live: nothing on this page acts.

The example is an illustration and never changes with the installation. Its labels must stay the
ones the chat uses (`components/chat/activity.tsx`, `approval.tsx`); when those change, this changes
with them.

## Accessibility

- One `h1`. The example is a `figure` labelled "Example task"; its mark is decorative, its drawn
  buttons are not focusable and are hidden from assistive technology as controls.
- The two actions are links styled as buttons, 48 px high, reachable first by keyboard.
- Light and dark follow the person's setting; nothing on the page depends on colour alone.
- No motion.
