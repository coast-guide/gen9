import { describe, expect, it } from "vitest";

import { doneOf, doneWords, kcAction, removingKind } from "./account-actions";

const id = "0ec160b7-3deb-441b-966c-d4d4e6cd00ba";

describe("kcAction", () => {
  it("passes the account actions Settings offers", () => {
    for (const action of [
      "UPDATE_PASSWORD",
      "UPDATE_PROFILE",
      "CONFIGURE_TOTP",
      "CONFIGURE_RECOVERY_AUTHN_CODES",
      "webauthn-register-passwordless",
    ]) {
      expect(kcAction(action)).toBe(action);
    }
  });

  it("passes removing one credential by id", () => {
    expect(kcAction(`delete_credential:${id}`)).toBe(`delete_credential:${id}`);
  });

  it("drops anything else", () => {
    for (const action of [
      null,
      "",
      "delete_account",
      "update_password",
      "UPDATE_PASSWORD ",
      "delete_credential",
      "delete_credential:",
      "delete_credential:password-id", // Keycloak's placeholder for a federated credential type
      `delete_credential:${id.toUpperCase()}`,
      `delete_credential:${id}:x`,
      `delete_credential:${id}\n`,
      `x${"delete_credential:"}${id}`,
    ]) {
      expect(kcAction(action)).toBeUndefined();
    }
  });
});

describe("what Settings says after an account action", () => {
  it("names each action by what it did", () => {
    expect(doneWords([doneOf("UPDATE_PASSWORD")!])).toBe("Password changed.");
    expect(doneWords([doneOf("webauthn-register-passwordless")!])).toBe("Passkey added.");
    expect(doneWords([doneOf(`delete_credential:${id}`, removingKind("passkey"))!])).toBe("Passkey removed.");
  });

  it("names a first authenticator app and its codes together, and codes removed with the last app", () => {
    expect(doneWords([doneOf("CONFIGURE_TOTP")!, doneOf("CONFIGURE_RECOVERY_AUTHN_CODES")!])).toBe(
      "Authenticator app set up. Recovery codes saved.",
    );
    expect(doneWords(["removed-authenticator", "pruned-recovery-codes"])).toBe(
      "Authenticator app removed. Its recovery codes went with it.",
    );
  });

  it("takes a removal's kind only from the list, and reads anything else as before", () => {
    expect(removingKind("password")).toBeUndefined();
    expect(doneOf(`delete_credential:${id}`, removingKind("password"))).toBe("removed");
    expect(doneOf(undefined)).toBeUndefined();
    expect(doneWords(["1"])).toBe("Your account was updated.");
    expect(doneWords(["<script>"])).toBe("Your account was updated.");
  });
});
