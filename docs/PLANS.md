# Plans: how long pieces of work stay resumable

Work that spans more than one session (a new capability, a migration, a redesign) gets a plan in
`docs/plans/<name>.md`. A plan is a living document: anyone, a person or an agent with no memory of
earlier sessions, must be able to resume the work from the plan and the repository alone. The
convention follows OpenAI's ExecPlans ([Using PLANS.md for multi-hour problem
solving](https://developers.openai.com/cookbook/articles/codex_exec_plans)) and Anthropic's
long-running harness practice ([Effective harnesses for long-running
agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)): a
progress log, the surprises and decisions behind the work, and a list of acceptance checks that
flip to passing only when verified.

## What a plan contains

A plan is prose first. It starts with **Purpose**: what someone can do afterwards that they could
not before, and how to see it working. Then four sections that must always be current:

- **Progress**: a checklist, one line per unit of work, each with the commit that did it. Plans
  carry no dates: git history dates them. Split an item into "done" and "remaining" rather than leaving it
  half-true. An item is checked only after it was verified live on the running stacks.
- **Surprises & Discoveries**: what behaved unexpectedly, with a short piece of evidence (a command
  and its output). This is what stops the next session from trying the same dead end.
- **Decision Log**: each decision, why, and the evidence (a link to a primary source, a probe, a
  measurement). When a decision is reversed, add a new entry; don't rewrite the old one.
- **Outcomes & Retrospective**: at each milestone, what was achieved against the purpose, what is
  left, and what we learned.

Then the orientation a newcomer needs: **Context** (the current state, key files by path, terms
defined), **Plan of work** (milestones as short narratives: goal, work, result, proof), **Validation**
(what to run and what to observe), and **Interfaces** (the modules, types and endpoints that must
exist).

## Acceptance list

Next to the plan, `docs/plans/<name>-acceptance.json` lists the user-visible behaviors the plan
promises, each with how it is checked:

    { "id": "runs.reload-mid-answer", "milestone": "1", "behavior": "…",
      "check": "e2e/runs.mjs", "passes": true }

Only `passes` changes, and only after the named check ran and passed. Items are not
removed or reworded to make them pass; a behavior that changes gets a new item. JSON because agents
edit it less freely than prose, and scripts can read it.

## Working on a plan

At the start of a session read the plan (Progress first), `git log`, and the acceptance list. Work
on the first unchecked Progress item, one logical unit at a time. At every stopping point, before
the session ends or the context is summarized, update Progress (with the commit), add any surprise
and decision, and commit the plan with the work. Keep the pull request description's checklist in
step with Progress.
