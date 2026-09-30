import { createRemoteJWKSet, jwtVerify } from "jose";

import { oidc } from "@/lib/auth/oidc";
import { readBounded } from "@/lib/bounded-body";
import { deleteSessionsBySid, deleteSessionsBySub, rememberLogoutTokenId } from "@/lib/auth/store";
import { env } from "@/lib/env";

// OpenID Connect Back-Channel Logout 1.0: Keycloak POSTs a signed logout token when a session ends
// elsewhere (sign-out in another app, admin action, "sign out everywhere"). We drop the matching
// server-side sessions, so the browser is signed out on its next request.
const LOGOUT_EVENT = "http://schemas.openid.net/event/backchannel-logout";

let jwks: ReturnType<typeof createRemoteJWKSet> | undefined;

async function keys() {
  jwks ??= createRemoteJWKSet(new URL((await oidc()).serverMetadata().jwks_uri!));
  return jwks;
}

function reply(status: number, body?: object) {
  return Response.json(body ?? {}, { status, headers: { "Cache-Control": "no-store" } });
}

// A logout token is a signed JWT of a few KiB. The body is read before anything is verified, so
// at most this much of it: 100 MB had been read whole, 300 MiB more held (P4-D1)
const MAX_LOGOUT_BYTES = 64 * 1024;

export async function POST(request: Request) {
  if (Number(request.headers.get("content-length") ?? 0) > MAX_LOGOUT_BYTES) return reply(413, { error: "invalid_request" });
  const raw = await readBounded(request.body, MAX_LOGOUT_BYTES);
  if (raw === null) return reply(413, { error: "invalid_request" });
  // The spec's form encoding (application/x-www-form-urlencoded)
  const token = new URLSearchParams(raw).get("logout_token");
  if (!token) return reply(400, { error: "invalid_request" });

  try {
    const { payload } = await jwtVerify(token, await keys(), {
      issuer: env().KEYCLOAK_ISSUER,
      audience: env().KEYCLOAK_CLIENT_ID,
      algorithms: ["RS256"],
      maxTokenAge: "5 minutes",
      requiredClaims: ["iat", "jti"],
    });
    const events = payload.events as Record<string, unknown> | undefined;
    if (!events || !(LOGOUT_EVENT in events) || "nonce" in payload) throw new Error("not a logout token");
    if (!payload.sid && !payload.sub) throw new Error("logout token names neither sid nor sub");
    if (!(await rememberLogoutTokenId(String(payload.jti), 300))) throw new Error("replayed logout token");

    const dropped = payload.sid
      ? await deleteSessionsBySid(String(payload.sid))
      : await deleteSessionsBySub(String(payload.sub));
    console.info(`[auth/backchannel-logout] ${payload.sid ? "sid" : "sub"} logged out, ${dropped} session(s) removed`);
    return reply(200);
  } catch (error) {
    console.warn("[auth/backchannel-logout] rejected:", (error as Error).message);
    return reply(400, { error: "invalid_request" });
  }
}
