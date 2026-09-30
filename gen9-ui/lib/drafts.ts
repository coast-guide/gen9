/**
 * A message not yet sent, kept in the tab (sessionStorage) until it's accepted: a session that
 * ended while its person was away sends them to sign in and back, and a page can reload, and
 * neither should cost what they typed (WCAG 2.2 SC 2.2.5, Re-authenticating: continue the activity
 * without loss of data). Kept per person, so someone else signing in on the tab doesn't see it;
 * gone with the tab, and on sign-out. A browser that refuses storage (a private window, storage
 * turned off) keeps nothing, and the composer works as before.
 */

const PREFIX = "gen9-draft:";

const key = (owner: string, chat: string | null) => `${PREFIX}${owner}:${chat ?? "new"}`;

/** The kept draft of this person's chat (`null`: a chat not made yet), or "". */
export function loadDraft(owner: string, chat: string | null): string {
  try {
    return sessionStorage.getItem(key(owner, chat)) ?? "";
  } catch {
    return "";
  }
}

/** Keep `text` as the draft of this person's chat; an empty one removes it. */
export function saveDraft(owner: string, chat: string | null, text: string): void {
  try {
    if (text.trim()) sessionStorage.setItem(key(owner, chat), text);
    else sessionStorage.removeItem(key(owner, chat));
  } catch {
    // storage refused: nothing kept
  }
}

/** Every kept draft on this tab, whoever's: on sign-out. */
export function clearDrafts(): void {
  try {
    for (const k of Object.keys(sessionStorage)) if (k.startsWith(PREFIX)) sessionStorage.removeItem(k);
  } catch {
    // storage refused: nothing was kept
  }
}
