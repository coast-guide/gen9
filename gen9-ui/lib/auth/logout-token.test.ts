import { type CryptoKey, createLocalJWKSet, exportJWK, generateKeyPair, SignJWT } from "jose";
import { beforeAll, describe, expect, it } from "vitest";

import { LOGOUT_EVENT, verifyLogoutToken } from "@/lib/auth/logout-token";

const ISSUER = "http://localhost:15000/realms/gen9";
let privateKey: CryptoKey;
let keys: ReturnType<typeof createLocalJWKSet>;

beforeAll(async () => {
  const pair = await generateKeyPair("RS256");
  privateKey = pair.privateKey;
  keys = createLocalJWKSet({ keys: [{ ...(await exportJWK(pair.publicKey)), kid: "k1", alg: "RS256" }] });
});

/** A token as Keycloak sends one, with `change` applied: header and claims. */
function token(change: { typ?: string | null; claims?: Record<string, unknown> } = {}) {
  const claims = { sid: "s1", sub: "u1", events: { [LOGOUT_EVENT]: {} }, jti: "j1", ...change.claims };
  const header = { alg: "RS256", kid: "k1", ...(change.typ === null ? {} : { typ: change.typ ?? "logout+jwt" }) };
  return new SignJWT(claims).setProtectedHeader(header).setIssuer(ISSUER).setAudience("gen9-ui").setIssuedAt().sign(privateKey);
}
const verified = async (jwt: Promise<string>) => verifyLogoutToken(await jwt, keys, ISSUER, "gen9-ui").then((p) => p.sid, () => "refused");

describe("verifyLogoutToken", () => {
  it("takes Keycloak's logout token", async () => {
    expect(await verified(token())).toBe("s1");
  });
  // P7-B3, OWASP ASVS 5.0 10.5.5: another token of the realm, signed by the same key, mustn't pass as one
  it("refuses one not typed logout+jwt", async () => {
    expect(await verified(token({ typ: "JWT" }))).toBe("refused");
    expect(await verified(token({ typ: null }))).toBe("refused");
  });
  it("refuses one with a nonce, or without the logout event", async () => {
    expect(await verified(token({ claims: { nonce: "n" } }))).toBe("refused");
    expect(await verified(token({ claims: { events: {} } }))).toBe("refused");
  });
  it("refuses one for another client, or naming neither a session nor a person", async () => {
    expect(await verifyLogoutToken(await token(), keys, ISSUER, "temporal-ui").then(() => "taken", () => "refused")).toBe("refused");
    expect(await verified(token({ claims: { sid: undefined, sub: undefined } }))).toBe("refused");
  });
});
