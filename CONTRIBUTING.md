# Contributing to Gen9

Thank you for taking the time. This page says how to propose a change; the rules the work follows
are in [AGENTS.md](AGENTS.md), for people and AI coding agents alike.

## Before you start

- **A bug or a question:** [open an issue](https://github.com/coast-guide/gen9/issues/new/choose).
- **A vulnerability:** never in an issue; report it privately, as [SECURITY.md](SECURITY.md) says.
- **A larger change** (a feature, a new stack, a change in how something behaves): open an issue
  first, so we agree on it before you build it. Work here is researched and planned before it is
  written ([AGENTS.md, "Before any new piece of work"](AGENTS.md#before-any-new-piece-of-work-mandatory)).

## Set up

Run Gen9 on your machine as [docs/operations.md](docs/operations.md) says (`make setup`, `make up`).
How the repository is organised, how changes are checked and how to add a stack:
[docs/development.md](docs/development.md). Where each kind of knowledge lives:
[AGENTS.md, "Where things live"](AGENTS.md#where-things-live).

## Propose a change

1. Fork the repository and branch from `main`.
2. Make one change at a time, with its documentation in the same change
   ([AGENTS.md, "How we work"](AGENTS.md#how-we-work)).
3. Run the checks for what you changed ([AGENTS.md, "Checks"](AGENTS.md#checks)). For anything a
   person would notice, run it live on the stacks too, and say what you ran.
4. Write the commit message as a subject that says what changes and a body that says why and how
   it was verified; no co-author, "assisted by", session or "generated with" lines
   ([AGENTS.md, "Rules"](AGENTS.md#rules)).
5. Open a pull request and fill in its template. CI runs the checks that need no stacks; the
   maintainer reviews and squash-merges.

## With an AI coding agent

Welcome. Point it at [AGENTS.md](AGENTS.md) (most agents read it by themselves). You remain
responsible for what it writes: read it, verify it on the running stacks, and say in the pull
request what was verified.

## Conduct and licence

Everyone taking part follows the [code of conduct](CODE_OF_CONDUCT.md). What you contribute is
licensed under the [Apache License 2.0](LICENSE), as its section 5 says.
