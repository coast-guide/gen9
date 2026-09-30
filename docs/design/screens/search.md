# Search

Find a past chat by the words in it, by what it was about, or by a title half remembered.
Principles: 4 (found again), 3 (honest about limits), 9 (private), 7 (everyone).

Built: `gen9-ui/app/(app)/search/`, `components/search/`, checked by `e2e/search.mjs` in
Chrome and by `e2e/a11y.mjs`.

## Where

- **Sidebar:** "Search", right under "New chat", with a search icon. On phones it's in the sheet,
  in the same place.
- **Route:** `/search?q=…&mode=…`. The query lives in the URL, so Back, reload and a bookmark
  return to the same results.

## Layout

```
Search                                            (headline)
Your past chats, by their words, their meaning, or a title you half remember.

[ (search icon)  Search your chats…                              (clear) ]
( All ) ( Words ) ( Meaning ) ( Title )           mode switch, All selected

3 chats                                           (announced to screen readers)
┌──────────────────────────────────────────────────────────────────┐
│ How should I tune Postgres autovacuum for a table with heavy …   │  title
│ Lower autovacuum_vacuum_scale_factor for the table, raise the …  │  snippet
│ 2 days ago                                                       │  date
├──────────────────────────────────────────────────────────────────┤
│ …                                                                │
└──────────────────────────────────────────────────────────────────┘
```

Desktop: the same column width as a conversation (`max-w-3xl`), centered. Phone: full width with
the page gutters. The search field sits at the top, where people look for search.

## Behavior

- **When it searches:** on Enter, and when the mode changes with a query present. Not on every
  keystroke: "All" and "Meaning" embed the query through the model router, which costs money and
  counts against the person's budget.
- **Modes:** "All" (the default), "Words" (`keyword`), "Meaning" (`semantic`), "Title"
  (`fuzzy`). Chinese, Japanese and Thai, written without spaces, are found by a word inside a
  sentence in every mode: such a query is matched as text rather than by Postgres's word
  parser, which reads a whole run of them as one word (gen9-agent's README, "Search"). "All"
  shows the matches by words at once (tens of milliseconds), then "More by
  meaning" under them, the chats not listed yet, once the query is embedded (0.7 to 13 s,
  measured). Appended rather than ranked together, so nothing moves under the cursor
  or focus meanwhile; the API's `hybrid` ranking stays for the terminal, MCP and the agent.
- **One row per chat:** the API returns each chat once, with its best-matching turn, so a long
  chat about the same thing can't push the others out.
- **A row** links to `/chat/<id>`. Its title is the chat's. The snippet is plain text around the
  first matching word, the answer before the question, since the question is usually the title
  (none for a title match). The date is relative, with the full date in a tooltip.
- **Only close matches by meaning:** gen9-agent keeps those within 60% of the best one's
  similarity and, for a model it has measured, above that model's floor, so "All" isn't padded
  with the nearest unrelated chats, and a question no chat is about finds none by meaning. Word
  matches always show.
- **Autofocus** on the field when the page opens without a query.

## States

| State | What shows |
| --- | --- |
| No query yet | The field and modes; a short line: "Try a phrase you remember, or what the chat was about." |
| Searching | Three skeleton rows; the field stays usable |
| Results | "N chats", then the rows. In All: "N chats match its words" (or "No chats match its words"), the rows, then "Looking for chats about it by meaning…" until "More by meaning", "N more chats about it" and their rows (or "No other chats about it.") |
| Nothing found | "No chats match "…"." With "Words", "Meaning" or "Title" selected, add: "Try All." |
| Search by meaning down, All | The results by words, and under them: "Search by meaning is unavailable right now. Showing matches by words." |
| Search by meaning down, Meaning | "Search by meaning is unavailable right now. Try Words." (503) |
| Over the usage limit | The usage-limit message (429), and the Words mode still works |
| Signed out | Sign-in, as every page |

## Accessibility

- The form has `role="search"`, and the field's label is "Search your chats".
- The mode switch is a radio group with arrow keys.
- The result count is in a polite live region, and each outcome is a section named "Results for
  “…”, by <mode>".
- Rows are links in a list, and focus goes from the field to the modes to the first result.
- Touch targets are 44 px. It's checked by `e2e/a11y.mjs` at phone and desktop sizes, light and
  dark.
