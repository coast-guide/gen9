// Keycloak application-initiated actions (AIA) the app may start: the user completes them on
// Keycloak's pages and comes back through the normal callback.
const ACCOUNT_ACTIONS = new Set([
  "UPDATE_PASSWORD",
  "UPDATE_PROFILE",
  "CONFIGURE_TOTP",
  "CONFIGURE_RECOVERY_AUTHN_CODES",
  "webauthn-register-passwordless",
]);
// Remove one of the user's own credentials; Keycloak asks to confirm and never removes the password
const DELETE_CREDENTIAL = /^delete_credential:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

/** The `kc_action` to send for a requested action, or undefined if it isn't one the app offers. */
export function kcAction(action: string | null): string | undefined {
  if (!action) return undefined;
  return ACCOUNT_ACTIONS.has(action) || DELETE_CREDENTIAL.test(action) ? action : undefined;
}

// What a finished action did, as the callback names it in `?updated=` (from the transaction the
// app stored, never from Keycloak's redirect), and what Settings then says (P3-D2)
const DONE: Record<string, string> = {
  UPDATE_PASSWORD: "password",
  UPDATE_PROFILE: "profile",
  CONFIGURE_TOTP: "authenticator",
  CONFIGURE_RECOVERY_AUTHN_CODES: "recovery-codes",
  "webauthn-register-passwordless": "passkey",
};
// What a removal link says it removes (`?removing=`): Keycloak's delete_credential names an id only
const REMOVABLE = new Set(["authenticator", "passkey", "recovery-codes"]);
const WORDS: Record<string, string> = {
  password: "Password changed.",
  profile: "Profile saved.",
  authenticator: "Authenticator app set up.",
  "recovery-codes": "Recovery codes saved.",
  passkey: "Passkey added.",
  "removed-authenticator": "Authenticator app removed.",
  "removed-passkey": "Passkey removed.",
  "removed-recovery-codes": "Recovery codes removed.",
  // The last app's removal takes its recovery codes (the auth callback)
  "pruned-recovery-codes": "Its recovery codes went with it.",
  removed: "Removed.",
};

/** The kind a removal link names, if it is one Settings offers. */
export function removingKind(value: string | null): string | undefined {
  return value && REMOVABLE.has(value) ? value : undefined;
}

/** What a finished action did, for `?updated=`. */
export function doneOf(action: string | undefined, removing?: string): string | undefined {
  if (!action) return undefined;
  if (DELETE_CREDENTIAL.test(action)) return removing ? `removed-${removing}` : "removed";
  return DONE[action];
}

/** What Settings says after its account actions; an unknown one reads as before. */
export function doneWords(updated: string[]): string {
  const known = updated.map((u) => WORDS[u]).filter(Boolean);
  if (known.length) return known.join(" ");
  return "Your account was updated.";
}
