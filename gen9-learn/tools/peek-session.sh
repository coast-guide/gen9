#!/usr/bin/env bash
# Show what gen9-ui holds for one user's web sessions: the record's fields and its access token's
# claims, decrypted inside gen9-ui's own container with its own key. Read-only. It never prints a
# token, a cookie or a key: tokens are shown as their decoded claims only.
#
#   gen9-learn/tools/peek-session.sh ada@gen9.test
set -euo pipefail
cd "$(dirname "$0")/../.."

email=${1:?usage: gen9-learn/tools/peek-session.sh EMAIL}
sub=$(docker exec gen9-keycloak-postgres-1 psql -U keycloak -d keycloak -tAc \
  "select id from user_entity where email = '${email//\'/}' and realm_id = (select id from realm where name = 'gen9')")
[ -n "$sub" ] || { echo "no Keycloak user $email" >&2; exit 1; }

vk() { docker exec gen9-ui-valkey-1 sh -c "REDISCLI_AUTH=\"\$VALKEY_PASSWORD\" valkey-cli $*"; }
hashes=$(vk SMEMBERS "gen9:session-by-sub:$sub")
[ -n "$hashes" ] || { echo "$email ($sub) has no web session in Valkey"; exit 0; }

# The unsealing mirrors gen9-ui/lib/auth/crypto.ts: HKDF-SHA256(SESSION_SECRET) -> AES-256-GCM,
# stored as base64url(iv[12] | tag[16] | ciphertext)
unseal='
const { createDecipheriv, hkdfSync } = require("node:crypto");
let sealed = ""; process.stdin.on("data", (d) => (sealed += d)).on("end", () => {
  sealed = sealed.trim();
  if (!sealed) return console.log("  (expired: the index still names it, the record is gone)");
  const key = Buffer.from(hkdfSync("sha256", Buffer.from(process.env.SESSION_SECRET, "hex"), "", "gen9-ui session store v1", 32));
  const raw = Buffer.from(sealed, "base64url");
  const d = createDecipheriv("aes-256-gcm", key, raw.subarray(0, 12)); d.setAuthTag(raw.subarray(12, 28));
  const r = JSON.parse(Buffer.concat([d.update(raw.subarray(28)), d.final()]).toString("utf8"));
  const claims = (jwt) => JSON.parse(Buffer.from(jwt.split(".")[1], "base64url").toString("utf8"));
  const at = claims(r.accessToken), when = (ms) => new Date(ms).toISOString();
  console.log(JSON.stringify({
    record: { sub: r.sub, sid: r.sid, email: r.email, roles: r.roles, authTime: r.authTime && when(r.authTime * 1000),
      createdAt: when(r.createdAt), accessTokenExpiresAt: when(r.accessTokenExpiresAt),
      refreshTokenExpiresAt: when(r.refreshTokenExpiresAt), hasRefreshToken: !!r.refreshToken, hasIdToken: !!r.idToken },
    accessTokenClaims: { iss: at.iss, aud: at.aud, azp: at.azp, typ: at.typ, sub: at.sub, sid: at.sid, scope: at.scope,
      realm_roles: at.realm_access?.roles, auth_time: at.auth_time && when(at.auth_time * 1000),
      iat: when(at.iat * 1000), exp: when(at.exp * 1000), email: at.email },
  }, null, 2));
});'
for hash in $hashes; do
  echo "session ${hash:0:8}… (Valkey key gen9:session:${hash:0:8}…, expires in $(vk TTL "gen9:session:$hash") s)"
  vk GET "gen9:session:$hash" | docker exec -i gen9-ui-prod-1 node -e "$unseal"
done
