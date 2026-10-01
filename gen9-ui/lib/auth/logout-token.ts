import { type JWTPayload, type JWTVerifyGetKey, jwtVerify } from "jose";

// OpenID Connect Back-Channel Logout 1.0: the event that makes a JWT a logout token
export const LOGOUT_EVENT = "http://schemas.openid.net/event/backchannel-logout";

/** A logout token's claims once it's checked, else it throws: signed with the realm's key (RS256), from the
 * issuer, for this client, at most 5 minutes old, typed `logout+jwt` (Keycloak types it; OWASP ASVS 5.0 10.5.5:
 * no other token of the realm passes as one), with the logout event and no nonce, naming a session or a person. */
export async function verifyLogoutToken(token: string, keys: JWTVerifyGetKey, issuer: string, audience: string): Promise<JWTPayload> {
  const { payload } = await jwtVerify(token, keys, {
    issuer,
    audience,
    algorithms: ["RS256"],
    typ: "logout+jwt",
    maxTokenAge: "5 minutes",
    requiredClaims: ["iat", "jti"],
  });
  const events = payload.events as Record<string, unknown> | undefined;
  if (!events || !(LOGOUT_EVENT in events) || "nonce" in payload) throw new Error("not a logout token");
  if (!payload.sid && !payload.sub) throw new Error("logout token names neither sid nor sub");
  return payload;
}
