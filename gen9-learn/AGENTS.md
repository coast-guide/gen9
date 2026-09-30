# gen9-learn: rules for agents and contributors

`index.html` teaches maintainers how Gen9 works by having them trace one user through every service and store. Its value is that every step, command and output is true of the running system. Keep it that way.

## Never write from memory

- Every observable claim in `index.html` (a status code, a cookie attribute, a table name, an event type, a log line, a button label) must come from a run of `verify/`. Before adding or changing a step, add the matching check to `verify/batches/bN-*.mjs`, run it, and copy what it observed.
- Button and page labels change: check them against the live UI (the verifier clicks them by their text). Example of drift caught this way: the button is "Sign out other sessions", not "other browsers".
- Reading one service's code isn't observing the flow. The page once said deleting an account after 5 minutes gets a 401 from the agent (true of `auth.py`); the real-wait run showed gen9-ui's dialog asks to sign in again first, so the agent never sees the stale request. Only a full `node run.mjs` exercises that path.
- When a flow changes in gen9-ui, gen9-agent, gen9-keycloak or the Makefile, re-run `node run.mjs` (and `QUICK=1` is not enough if refresh or step-up changed) and update the page. Update "Last verified" in the footer.

## Commands in the page

- Standalone: each one works pasted on its own from the repo root, with no variables set by an earlier command. Derive what you need inline (see the `SUB=$(…)` examples).
- Use `trace@gen9.test` for the reader's user; the verifier substitutes its throwaway user.
- Tag every `<code>` inside `.cmd`: `data-check="run"` (must print something), `"run-any"` (may print nothing yet, such as a log search for a later event) or `"manual"` (interactive, destructive or long-running; exercise it in a batch script instead).
- Never print a secret: no tokens, cookies, client secrets or keys in outputs the page shows. `credential.secret_data` in the Keycloak DB is never selected. Commands that print the reader's own passwords (for DBeaver) are fine: they print to the reader's terminal, and the verifier never prints command output.

## The verifier

- `run.mjs` runs the story in order: `b1 b1d b2 b2d b2w b2wd b2x b2xd commands b3 b4 b4d b5 b5x b5xd b6d b6 b7 b7d`. A `…d` batch verifies a part's deeper steps (`li.step.deeper`) right after the part it deepens; `b6d` (the export) runs before `b6` deletes the account. The `commands` batch runs after b2xd because it needs a user with a web session, a chat, a trace, a scheduled task, a connector and an environment. A deeper batch that disturbs a stack puts it back in a `finally` (b2d stops the router and swaps the worker; b7d stops and resumes the agents). b2x serves a plugin marketplace with e2e's git server (`e2e/fixtures/git-server.mjs`, port 17805) and signs Ada in to add it; b5x signs an MCP client and an A2A agent in with PKCE, and uses the CLI's token for AG-UI.
- It types the throwaway user's password through Puppeteer, as `make e2e` does; browser tools driven by an agent must not type passwords.
- Gotchas it already handles: Keycloak's admin-cli token lives 60 s (`lib.mjs` refreshes it); a virtual authenticator left attached signs later pages in by itself (b3 removes it); Langfuse writes asynchronously (poll ClickHouse); session counts must filter the `gen9` realm (the configure step signs into `master`); *Try another way* is a form, not a link; Keycloak's `authChecker.js` can reload a sign-in page about 1 s after it loads, losing what was typed (`settled()` in `lib.mjs` waits it out, after every navigation); Keycloak asks for the password again before an account action once the last sign-in is over 5 minutes old (b3 checks both cases). Questions to the model ask for what the check needs exactly: a check once failed because its question asked for "the colour only" of `teal-<tag>`.
- Recordings in `verify/out/` are sanitized by `lib.mjs` (`sanitizeUrl`, cookie values dropped). Keep new recordings going through it.
- `reference.mjs` fails when the running system has something the Reference doesn't name: a service, volume, workflow, Activity, Schedule, API operation, table, route, setting, settings key or command, or a link within the page that leads nowhere. Adding one to Gen9 means adding its row, described from the code and linked to the step that shows it. It also fails when a pointer into the code (`<code>path:line</code>`, `:line` after one) no longer lands on what it names: each carries `data-at`, text from its first line, and a range `data-to`, from its last. Write both when adding a pointer; when code moves, the failure says the text's new line.

## Page design

- Gen9's own design system (`gen9-design/theme.css`): ink and hairlines, lapis for interaction and focus, gold only as the decorative "you are here" point, Instrument Sans for text, the system monospace for code. Tokens are copied into `index.html` so it works offline; keep them in sync.
- The map is the one signature element. Each step names what to watch in `data-watch` and its focus in `data-focus`; node ids are in the script's `NODES`.
- Steps are a sequence, so they're numbered. No all-caps labels, no decorative cards, no motion beyond the map's highlight (and none with reduced motion).
- Each step: one action (`.do`), optionally a `Predict` fold-out, then what to notice by place (DevTools, a store, logs, code). Keep outputs short and mark the one thing to see with `<mark>`. Each part ends with a few "Check yourself" questions, mixing multiple choice and recall.
- After editing, run `node page.mjs`: no script errors, no sideways scrolling at 390, 1100 and 1440 px, light and dark, and no serious or critical axe violations.
