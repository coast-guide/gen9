# Audit log (admins)

Who did what in Gen9, for the admins who answer for it. Principles: 3 (honest: what happened is
said as it happened, refusals included), 6 (calm: plain sentences, the technical route small and
muted), 9 (private: what someone did, never what their chats said).

## Today (built)

- **Header:** "Audit log", and "Who did what: admins' changes, people's security settings, and
  access Gen9 refused. Nobody can change or delete it."
- **Show:** "Everything" or "Refused access" (`?show=refused`), as a pair of links with the
  current one marked.
- **A row per event, newest first:** when (in the viewer's time zone), who (their email, "A
  person Gen9 no longer knows" after their account was deleted, "Gen9" for its own sweep of
  accounts deleted in Keycloak, "The operator" for `make stop-agents` and `make restore`), a
  "Refused" badge for
  refused access, and what happened as a sentence: "Made mary@example.com an admin", "Added the
  environment secret gh for api.github.com", "Tried to open someone else's chat". The route
  that did it (`PATCH /v1/admin/users/{user_id}`) is below, small and muted, for whoever needs to
  trace it. The action codes of gen9-agent's `audit.py` never reach the screen
  (`lib/audit-words.ts`; its test reads every code gen9-agent records and fails on one without
  words, after six had reached it).
- **Pages:** 50 events a page, "Older" and "Newest".
- In the sidebar and the account menu as "Audit log", for admins only. Anyone else sees "You
  need admin access".

What's recorded, and what never is (tokens, passwords, secret values, message text): gen9-agent's
README, "How auth works".

## Next (planned)

- Filters by person and by kind of action, if the log grows past what paging serves.
