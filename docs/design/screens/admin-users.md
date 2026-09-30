# Users (admins)

Who can sign in, and what an admin can do about each person. Principles: 2 (in charge: deleting
someone confirms and needs a fresh sign-in), 6 (calm), 9 (private: an admin sees accounts, never chats).

## Today (built)

- **Header:** "Users" and "N people can sign in to Gen9.", and a field to search by name or email: any part of either matches (Enter searches), and the line then reads "N people match "…"."
- **A row per person:** initials avatar, name with badges ("You", "Admin"), email and join date, and a
  menu of actions.
- **Actions** (the row's menu): "Unlock sign-in" (after too many attempts), "Send password reset",
  "Sign out everywhere", make or remove admin, enable or disable (disabling also signs out), and
  delete.
- **Confirmation and step-up:** a change to someone's access asks first, saying what it does, as
  Google Workspace, Okta and GitHub do: "Disable <email>?" (they can't sign in and are signed
  out, and anything Gen9 is doing for them stops: their queued, running and waiting answers; their
  chats stay), "Enable <email>?", "Make <email> an admin?" and "Remove admin access
  for <email>?" (at once). Delete asks "Delete <email>?", and needs the admin to have signed in
  within 5 minutes ("For your security, sign in again first"). An admin can't disable, demote
  or delete their own account here. Their own is deleted from Settings, where the only admin is
  asked to make someone else an admin first.
- In the sidebar and the account menu as "Users", for admins only.

## Next (planned)

- **Search index:** re-embedding runs on a Schedule (gen9-agent's `reindex-search`). An admin
  control to start it now, with its progress, belongs here if people need it.
  `POST /v1/admin/search/reindex` exists.
