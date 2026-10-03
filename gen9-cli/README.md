# gen9-cli

Gen9 from the terminal. `gen9 login` signs in with the [OAuth 2.0 Device Authorization Grant](https://www.rfc-editor.org/rfc/rfc8628): it shows a short code, you confirm it on the Gen9 sign-in page in any browser, and the terminal receives its own tokens. It never sees your password or passkey. Signed in, it says who you are and that Gen9 is an AI system and can be wrong, before your first question, as the web app does (docs/ai-act.md).

It is not a Docker stack: it runs on your machine against the running stacks (Keycloak for sign-in, gen9-agent for answers).

```bash
cd gen9-cli && uv sync
uv run gen9 login                 # prints a link and a code; confirm them in a browser
uv run gen9 whoami                # who you are, and which Gen9 you reached: its version and commit
uv run gen9 --version             # this terminal's own version
uv run gen9 ask "What does RFC 8628 say about slow_down?"
uv run gen9 ask --thread <id> "And about expired_token?"
uv run gen9 ask --attach data.csv "Plot it"  # a file for the chat's environment; --attach again for more
uv run gen9 search "expired token"  # your past chats, by words and meaning; --mode keyword|semantic|fuzzy
uv run gen9 search expired token   # quotes are optional: the rest of the line is the query (and for ask, the message)
uv run gen9 files <chat>            # the files a chat's environment shared; add a name to download one
uv run gen9 tasks                   # what Gen9 runs on its own; add, run, pause, resume, delete
uv run gen9 tasks add --every day --at 08:00 --done-when "- Every item has a date" "Brief" "…"  # checked against a rubric, retried in the chat until met (--tries 1-20)
uv run gen9 logout
```

When Gen9 needs something from you mid-task, `gen9 ask` asks it in the terminal: the question, with its choices numbered and "Other" last. Type a number or your own answer, and the answer goes on; Ctrl-C stops the run as usual. The chat's page in the browser shows the same question, and the first answer wins.

`gen9 ask --ask-first "…"` sets the chat to "Ask before acting": anything Gen9 would change (today, what it remembers about you) waits for your Allow. The terminal shows what it would change (a command as typed, a connector's arguments in full) and asks "Allow? [y/N]"; anything but y is Deny, and you can say what Gen9 should do instead. A character that is invisible, changes how text reads or would act on the terminal (an escape) is written as `<U+202E>` where it is, with a line saying so.

When a connector's server asks you something mid-task, `gen9 ask` says which connector asks, then asks its form field by field (choices numbered, a default in brackets) and "Send it? [Y/n]"; an address to open is printed in full and opens in your browser only if you say y.

If the answer fails in a way someone can fix (the model provider down or out of credits, your usage limit, with when it resets), `gen9 ask` says why and asks "Retry? [Y/n]": Enter continues the same answer from where it was, n stops it.

If the connection drops mid-answer (the API restarted, the network went), the answer goes on on the server, and `gen9 ask` says it lost the connection, reconnects and goes on after the last thing it showed (`Last-Event-ID`, as a browser's EventSource does). After five failed tries in a row, 1 to 16 seconds apart, it stops and says where to find the answer: the web app, or `gen9 ask --thread`.

An error gen9 didn't expect (an answer it can't read, say) is one line in the terminal, never a
Python traceback: the traceback goes to `last-error.txt` in the config folder (mode 0600, the last
one only), to report if it happens again.

| Setting | Default |
| --- | --- |
| `GEN9_ISSUER` | `http://localhost:15000/realms/gen9` (endpoints come from its discovery document) |
| `GEN9_API` | `http://localhost:17000` (gen9-agent) |
| `GEN9_CONFIG_DIR` | `~/.config/gen9` (holds `credentials.json`) |

## How sign-in works

| Step | What happens |
| --- | --- |
| Code | `POST …/auth/device` as the public client `gen9-cli`; Keycloak returns a user code, the page to confirm it on, and how often to poll |
| Confirm | You open the page, sign in as usual (password, passkey, two-factor), check that the code matches, and approve access for *Gen9 CLI*. The consent step shows which app you're letting in, which is RFC 8628's advice against someone phishing you with their own code |
| Poll | The terminal polls the token endpoint every `interval` seconds; `slow_down` adds 5 seconds; `access_denied` and `expired_token` stop it |
| Tokens | Saved to `credentials.json`, created with mode 0600 in a 0700 folder. The access token lasts 5 minutes and is refreshed 30 seconds before it expires; Keycloak rotates the refresh token each time, and the new one is saved. The sign-in is an ordinary Keycloak session, like the web app's: it ends after 30 minutes unused and 10 hours at most (the realm's SSO session limits), and then `gen9` says to run `gen9 login` again. Deliberately not an offline token: signing out everywhere, an admin's sign-out and disabling an account end it too |
| Sign out | `gen9 logout` revokes the terminal's refresh token (RFC 7009) and deletes the file. Your browser stays signed in. Signing that browser session out in Settings (*Where you're signed in*) or deleting the account also ends the terminal's access at its next refresh |

The access token's audience is `gen9-agent`, like the web app's, so gen9-agent applies the same rules to both (see gen9-agent/README.md).

## Test

```bash
uv run pytest     # device-flow polling rules, refresh and rotation, file permissions, event stream and reconnecting to it, questions, approvals, a connector's requests and Retry in the terminal
```
