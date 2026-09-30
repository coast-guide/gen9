# Scheduled

Built: `gen9-ui/app/(app)/scheduled/`, `components/scheduled/`.

Tasks Gen9 runs on its own, on a schedule or once at a time. Principles:
- 1, the work is visible: each run is a chat, and the task shows its last runs and its next one;
- 2, in charge: pause, resume, run now, edit and delete are one tap. A task keeps its own
  permission mode, and a run that needs Allow waits for the person;
- 3, honest about limits: a skipped run says why;
- 8, tell people when something changes: one email per run (Settings > Notifications), and one per
  firing for a task with a rubric;
- 6, calm.

References (docs/plans/harness.md, milestone 5):
- ChatGPT's Scheduled page: next run, pause, resume, edit, delete, and a cap on active tasks;
- Claude Code's scheduled tasks: presets, a permission mode per task, each run a new session, and
  history with skipped runs and why.

## Layout

At phone width first; one column, as Settings.

```
Scheduled
Gen9 runs these on its own, each time in a new chat.
┌──────────────────────────────────────────────────────────────┐
│ Morning brief                                   Active   ⋯   │
│ Every weekday at 09:00 (Asia/Kolkata) · Ask before acting    │
│ Next: tomorrow at 09:00                                       │
│ Last runs: Today 09:00 Done · Yesterday 09:00 Skipped: …     │
└──────────────────────────────────────────────────────────────┘
+ New task
```

- **Header:** "Scheduled" and "Gen9 runs these on its own, each time in a new chat."
- **A row per task:**
  - its name, and "Paused" or "Done" (a one-off that ran) as a badge;
  - its schedule in words, in the person's time zone, and its permission mode;
  - "Next: …" (relative);
  - its last runs, newest first, each a link to its chat with its state in the chat's words:
    "Working", "Needs you", "Done", "Didn't finish". A skipped one says why: "the last run was
    still going";
  - a menu: "Run now", "Pause" or "Resume", "Edit", "Delete…". Delete asks "Delete <name>? Its
    chats stay."
- **New task and Edit:** a form:
  - "Name";
  - "What Gen9 should do" (the prompt, as a chat message);
  - "When": Once, Every hour, Every day, Every weekday, Every week, then a time (and a date for
    once, a day for weekly). The time zone is shown by IANA's name, as the saved task says it
    ("Asia/Kolkata" where Chrome says "Asia/Calcutta"): the browser's for a new task; an edit keeps
    the task's own, and when that isn't where the browser is now, offers "Use <zone>, where you
    are now" (a person who moved), as a calendar event keeps its own zone;
  - "While it runs": "Ask before acting" or "Act, ask when unsure", as the chat's mode;
  - "Save".

  Refusals show in words: "At most 10 tasks at a time", "Pick a time in the future".
- **Empty:** "Nothing scheduled", then "New task" and one example: "Every weekday at 9, a brief
  on what changed in my field."
- In the sidebar as "Scheduled", after Search.

## API trigger

A task can also be fired over HTTP, by an alert, a deploy or a script, as Claude Code's routines
can (docs/plans/harness.md, milestone 5).
- In the task's menu: "API trigger…", a dialog:
  - the address to POST to;
  - "Make a token", or, when it has one, "Make a new token", which stops the old one working, and
    "Revoke";
  - a new token is shown once, in a read-only field with Copy, and a `curl` example. "Gen9 keeps
    only a fingerprint of it: copy it now." The task's row says "API trigger on".
- A caller's text reaches the run in a block labelled as data from the caller, and the task's own
  message says what to do with it.
- Limits: 30 fires an hour per task (Run now included) and 100 per person; a paused task refuses.

## Done when

A task may say what done looks like, as Anthropic's Managed Agents define outcomes
(docs/plans/harness.md, milestone 5). Principles 1 (the work is visible) and 3 (honest about
limits).
- In the form, after "While it runs": "Done when (optional)", a text area for the rubric, with
  the hint "Criteria its answer must meet. Gen9 checks each run against them and, if one isn't
  met, tries again in the same chat." Once there is a rubric, "Tries at most" (1 to 20, 3 by
  default) shows beside it.
- The row adds "Checked against a rubric, 3 tries at most" to its schedule line.
- A run's words: "Checking against its rubric" while it is graded (the row keeps refreshing);
  then "Done · meets its rubric in 2 tries", "Done · short of its rubric in 3 tries", or "Done ·
  its rubric doesn't apply".
- The chat shows each verdict under the answer it graded (chat.md, "A graded answer"), and a
  revision's message is the grader's findings, in the chat as any message.
- One email for the whole firing: "… is done", or "… didn't meet its rubric".

## States

| State | What shows |
| --- | --- |
| A run going | Its row's last run says "Working", linked to the chat |
| A run waiting for Allow | "Needs you" on the run, and on the chat in the sidebar |
| A run skipped | "Skipped: the last run was still going" |
| Paused | The badge; no "Next" |
| A one-off done | "Done", no "Next"; Run now still works |

## In the terminal

`gen9 tasks` lists them (name, schedule, next run, status); `gen9 tasks add`, `run`, `pause`,
`resume` and `delete` do the rest.
