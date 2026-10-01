// Keycloak as Gen9's authorization server, tried live against OWASP ASVS 5.0's V10.4
// (docs/plans/manual-e2e.md, P7-B1; gen9-keycloak/verify.sh checks the realm's settings). A
// throwaway person (Admin API) signs in through the pre-registered client `gen9-mcp`: authorization
// code, PKCE, a loopback redirect; Puppeteer types the password and gives consent.
//   1. PKCE: a request without code_challenge is refused, for gen9-mcp and for a client registered
//      by its metadata document (10.4.6); a code with the wrong code_verifier too
//   2. a code works once: exchanged again it's refused, and the tokens it gave stop refreshing
//      (10.4.2); one older than a minute is refused (10.4.3)
//   3. a refresh token works once: replayed, it's refused, and so is the one that replaced it
//      (10.4.5)
//   4. an offline token's session ends at a fixed time, at most 30 days on, that refreshing doesn't
//      move (10.4.8), and Gen9's "Sign out everywhere" ends it (Keycloak's own logout doesn't)
//   5. a confidential client without its secret, or with a wrong one, is refused (10.4.10)
//   6. gen9-agent's API refuses an ID token, and a real access token re-addressed to it unsigned
//      (9.1, 9.2, 10.3.1)
// No model call. The person, and the client registered by its document, are deleted at the end.
import { createHash, randomBytes } from "node:crypto";
import { createServer } from "node:http";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { launch } from "./browser.mjs";
import { signInTerminal } from "./signin.mjs";

const ROOT = new URL("..", import.meta.url).pathname;
const env = Object.fromEntries(
  readFileSync(`${ROOT}gen9-keycloak/.env`, "utf8")
    .split("\n")
    .filter((line) => /^[A-Z0-9_]+=/.test(line))
    .map((line) => [line.slice(0, line.indexOf("=")), line.slice(line.indexOf("=") + 1)]),
);
const KEYCLOAK = process.env.KEYCLOAK_URL ?? "http://localhost:15000";
const API = process.env.API_URL ?? "http://localhost:17000";
const REALM = `${KEYCLOAK}/realms/gen9/protocol/openid-connect`;
const EMAIL = `oauth-${Date.now()}@gen9.test`;
const PASSWORD = `e2e-${randomBytes(12).toString("hex")}`;

let failures = 0;
function check(ok, what, detail = "") {
  console.log(`${ok ? "ok  " : "FAIL"}  ${what}${detail ? `: ${detail}` : ""}`);
  if (!ok) failures++;
  return ok;
}
async function admin(method, path, body) {
  const token = await fetch(`${KEYCLOAK}/realms/master/protocol/openid-connect/token`, {
    method: "POST",
    body: new URLSearchParams({ grant_type: "password", client_id: "admin-cli", username: env.KC_BOOTSTRAP_ADMIN_USERNAME, password: env.KC_BOOTSTRAP_ADMIN_PASSWORD }),
  })
    .then((r) => r.json())
    .then((b) => b.access_token);
  const response = await fetch(`${KEYCLOAK}/admin/realms/gen9${path}`, { method, headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  if (!response.ok) throw new Error(`Keycloak ${method} ${path}: ${response.status}`);
  const text = await response.text();
  return text ? JSON.parse(text) : null;
}
const claims = (jwt) => JSON.parse(Buffer.from(jwt.split(".")[1], "base64url").toString());
const token = async (params) => {
  const response = await fetch(`${REALM}/token`, { method: "POST", body: new URLSearchParams(params) });
  return { status: response.status, body: await response.json().catch(() => ({})) };
};
const refused = (r) => `${r.status} ${r.body.error ?? ""}${r.body.error_description ? ` (${r.body.error_description})` : ""}`;

// The loopback redirect (RFC 8252): what Keycloak sends back to it, by the request's state
const callback = createServer();
await new Promise((resolve) => callback.listen(0, "127.0.0.1", resolve));
const REDIRECT = `http://127.0.0.1:${callback.address().port}/callback`;
const arrived = new Map();
callback.on("request", (req, res) => {
  const query = new URL(req.url, REDIRECT).searchParams;
  // Only a request this script made has a waiting function under its state
  const waiting = arrived.get(query.get("state") ?? "");
  if (typeof waiting === "function") waiting(Object.fromEntries(query));
  res.end("Done.");
});

// An authorization request in the person's browser: what came back to the redirect, and the
// verifier. `pkce: false` sends none
async function authorize(page, { clientId = "gen9-mcp", scope = "openid gen9-mcp", pkce = true } = {}) {
  const state = randomBytes(8).toString("hex");
  const verifier = randomBytes(32).toString("base64url");
  const url = new URL(`${REALM}/auth`);
  url.search = new URLSearchParams({
    client_id: clientId,
    response_type: "code",
    redirect_uri: REDIRECT,
    scope,
    state,
    ...(pkce ? { code_challenge: createHash("sha256").update(verifier).digest("base64url"), code_challenge_method: "S256" } : {}),
  });
  const back = new Promise((resolve) => arrived.set(state, resolve));
  await page.goto(url.toString(), { waitUntil: "networkidle0" });
  if (await page.$("#username")) {
    await page.type("#username", EMAIL);
    await page.type("#password", PASSWORD);
    await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }).catch(() => {}), page.click("#kc-login")]);
  }
  if (await page.$('button[name="accept"]')) await Promise.all([page.waitForNavigation().catch(() => {}), page.click('button[name="accept"]')]);
  const answer = await Promise.race([back, new Promise((resolve) => setTimeout(() => resolve({ page: page.url() }), 15_000))]);
  return { ...answer, verifier };
}
const exchange = (code, verifier, clientId = "gen9-mcp") =>
  token({ grant_type: "authorization_code", client_id: clientId, code, redirect_uri: REDIRECT, code_verifier: verifier });
const refresh = (refreshToken, clientId = "gen9-mcp") => token({ grant_type: "refresh_token", client_id: clientId, refresh_token: refreshToken });

await admin("POST", "/users", { username: EMAIL, email: EMAIL, firstName: "OAuth", lastName: "Check", enabled: true, emailVerified: true, credentials: [{ type: "password", value: PASSWORD, temporary: false }] });
const id = (await admin("GET", `/users?email=${encodeURIComponent(EMAIL)}&exact=true`))[0].id;
let documentUrl;
const browser = await launch({ headless: !process.env.HEADED });
try {
  // 1. PKCE
  const page = await (await browser.createBrowserContext()).newPage();
  const noPkce = await authorize(page, { pkce: false });
  check(noPkce.error === "invalid_request" && !noPkce.code, "gen9-mcp: a request without code_challenge is refused", noPkce.error_description ?? noPkce.page ?? "");
  const wrong = await authorize(page);
  const wrongVerifier = await exchange(wrong.code, randomBytes(32).toString("base64url"));
  check(wrongVerifier.status === 400, "a code with the wrong code_verifier is refused", refused(wrongVerifier));

  // 2. Codes
  const once = await authorize(page);
  const first = await exchange(once.code, once.verifier);
  const second = await exchange(once.code, once.verifier);
  const afterReuse = await refresh(first.body.refresh_token ?? "");
  check(first.status === 200 && second.status === 400 && afterReuse.status === 400, "a code works once: the second exchange is refused, and the first one's tokens stop refreshing", `${first.status}, ${refused(second)}, then ${refused(afterReuse)}`);
  const old = await authorize(page);
  await new Promise((resolve) => setTimeout(resolve, 61_000));
  const late = await exchange(old.code, old.verifier);
  check(late.status === 400, "a code older than a minute is refused", refused(late));

  // 3. Refresh tokens
  const fresh = await authorize(page);
  const issued = await exchange(fresh.code, fresh.verifier);
  const next = await refresh(issued.body.refresh_token);
  const replayed = await refresh(issued.body.refresh_token);
  const newest = await refresh(next.body.refresh_token ?? "");
  check(next.status === 200 && replayed.status === 400 && newest.status === 400, "a refresh token works once: replayed it's refused, and so is the one that replaced it", `${next.status}, ${refused(replayed)}, then ${refused(newest)}`);

  // 4. Offline access. The first token's end may come from the browser's sign-in; from then on the
  // offline session's own end holds, however often it's refreshed
  const away = await authorize(page, { scope: "openid gen9-mcp offline_access" });
  const offline = await exchange(away.code, away.verifier);
  const renewed = await refresh(offline.body.refresh_token ?? "");
  await new Promise((resolve) => setTimeout(resolve, 3_000));
  const twice = await refresh(renewed.body.refresh_token ?? "");
  const [earlier, later] = [claims(renewed.body.refresh_token ?? "e30.e30."), claims(twice.body.refresh_token ?? "e30.e30.")];
  const ends = (claim) => (typeof claim.exp === "number" ? new Date(claim.exp * 1000).toISOString() : "never");
  check(
    earlier.typ === "Offline" && typeof earlier.exp === "number" && later.exp === earlier.exp && earlier.exp - Date.now() / 1000 <= 30 * 86_400,
    "an offline token's session ends at a fixed time, at most 30 days on: refreshing doesn't move it",
    `${earlier.typ}, ends ${ends(earlier)}, after another refresh ${twice.status === 200 ? ends(later) : refused(twice)}`,
  );
  // Signed out everywhere, as the person does it in Settings: Gen9's API, with their terminal's token
  const configDir = mkdtempSync(join(tmpdir(), "gen9-oauth-"));
  try {
    await signInTerminal({ email: EMAIL, password: PASSWORD, configDir });
    const cliToken = JSON.parse(readFileSync(join(configDir, "credentials.json"), "utf8")).access_token;
    const out = await fetch(`${API}/v1/me/sign-out-everywhere`, { method: "POST", headers: { Authorization: `Bearer ${cliToken}` } });
    const signedOut = await refresh(twice.body.refresh_token ?? "");
    check(out.status === 204 && signedOut.status === 400, "Sign out everywhere ends the agent's offline token too", `${out.status}, then ${refused(signedOut)}`);
  } finally {
    rmSync(configDir, { recursive: true, force: true });
  }

  // 1 again. A client registered by its metadata document (served where Keycloak's container
  // reaches it: host.docker.internal, allowed over http in development)
  const docs = createServer();
  await new Promise((resolve) => docs.listen(0, "0.0.0.0", resolve));
  documentUrl = `http://host.docker.internal:${docs.address().port}/gen9-oauth-client.json`;
  docs.on("request", (req, res) => {
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify({ client_id: documentUrl, client_name: "Gen9 e2e (OAuth)", redirect_uris: [REDIRECT], grant_types: ["authorization_code", "refresh_token"], response_types: ["code"], token_endpoint_auth_method: "none" }));
  });
  try {
    const own = await authorize(page, { clientId: documentUrl, pkce: false });
    check(own.error === "invalid_request" && !own.code, "a client registered by its metadata document: a request without code_challenge is refused", own.error_description ?? own.page ?? "");
  } finally {
    docs.close();
  }

  // 5. Confidential clients
  const secretless = await token({ grant_type: "client_credentials", client_id: "gen9-agent" });
  const wrongSecret = await token({ grant_type: "client_credentials", client_id: "gen9-agent", client_secret: "not-it" });
  check(secretless.status === 401 && wrongSecret.status === 401, "a confidential client without its secret, or with a wrong one, is refused", `${refused(secretless)}; ${refused(wrongSecret)}`);

  // 6. The API takes only access tokens issued for it, signed (ASVS 5.0 9.1, 9.2, 10.3.1): an ID
  // token, and a real access token re-addressed to the API (aud, azp) with `alg: none`
  const onApi = async (bearer) => (await fetch(`${API}/v1/me`, { headers: { Authorization: `Bearer ${bearer}` } })).status;
  const part = (value) => Buffer.from(JSON.stringify(value)).toString("base64url");
  const unsigned = `${part({ alg: "none", typ: "JWT" })}.${part({ ...claims(first.body.access_token ?? "e30.e30."), aud: "gen9-agent", azp: "gen9-ui" })}.`;
  const onApiStatuses = [await onApi(first.body.id_token ?? ""), await onApi(unsigned)];
  check(onApiStatuses.every((s) => s === 401), "the API refuses an ID token, and an unsigned token", onApiStatuses.join(", "));
} finally {
  await browser.close();
  callback.close();
  await admin("DELETE", `/users/${id}`).catch(() => {});
  if (documentUrl) {
    const [kept] = (await admin("GET", `/clients?clientId=${encodeURIComponent(documentUrl)}`).catch(() => [])) ?? [];
    if (kept) await admin("DELETE", `/clients/${kept.id}`).catch(() => {});
  }
}
console.log(failures ? `\n${failures} check(s) failed` : "\nall checks passed");
process.exit(failures ? 1 : 0);
