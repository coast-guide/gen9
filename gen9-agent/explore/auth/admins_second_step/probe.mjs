// M10 probe against the throwaway Keycloak (exp-kc-2fa, port 25080): how Gen9's new browser flow
// treats admins and members. Never the running stack.
import { createRequire } from "node:module";
import { createHash, createHmac, randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";

const require = createRequire(new URL("../../../../e2e/package.json", import.meta.url));
const puppeteer = require("puppeteer-core");
const KC = "http://localhost:25080";
const env = Object.fromEntries(
  readFileSync(new URL("./.env", import.meta.url), "utf8").split("\n").filter(Boolean).map((l) => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]),
);
const results = [];
const note = (what, value) => { results.push([what, value]); console.log(`${what}: ${value}`); };

async function adminToken() {
  const r = await fetch(`${KC}/realms/master/protocol/openid-connect/token`, {
    method: "POST",
    body: new URLSearchParams({ grant_type: "password", client_id: "admin-cli", username: "admin", password: env.KC_BOOTSTRAP_ADMIN_PASSWORD }),
  });
  return (await r.json()).access_token;
}
async function admin(path, init = {}) {
  const r = await fetch(`${KC}/admin/realms/gen9${path}`, { ...init, headers: { Authorization: `Bearer ${await adminToken()}`, "Content-Type": "application/json" } });
  if (!r.ok) throw new Error(`${init.method ?? "GET"} ${path}: ${r.status} ${await r.text()}`);
  return r.status === 201 || r.status === 204 ? r.headers.get("location") : r.json();
}
const realm = await admin("");
const policy = { period: realm.otpPolicyPeriod, digits: realm.otpPolicyDigits, algorithm: realm.otpPolicyAlgorithm.replace("Hmac", "").toLowerCase() };
note("realm OTP policy", JSON.stringify({ ...policy, reusable: realm.otpPolicyCodeReusable, lookAhead: realm.otpPolicyLookAheadWindow }));
const step = () => Math.floor(Date.now() / 1000 / policy.period);
function totp(secret, counter = step()) {
  const m = Buffer.alloc(8);
  m.writeBigUInt64BE(BigInt(counter));
  const h = createHmac(policy.algorithm, Buffer.from(secret, "utf8")).update(m).digest();
  const o = h[h.length - 1] & 0xf;
  return String((h.readUInt32BE(o) & 0x7fffffff) % 10 ** policy.digits).padStart(policy.digits, "0");
}
async function nextStep(after) { while (step() <= after) await new Promise((r) => setTimeout(r, 500)); return step(); }

const groups = await admin("/groups?search=admins&exact=true");
const adminsGroup = groups[0].id;
async function makeUser(name, isAdmin) {
  const password = `gen9-${randomBytes(9).toString("hex")}`;
  const loc = await admin("/users", { method: "POST", body: JSON.stringify({ username: `${name}@probe.test`, email: `${name}@probe.test`, emailVerified: true, enabled: true, firstName: name, lastName: "Probe", credentials: [{ type: "password", value: password, temporary: false }] }) });
  const id = loc.split("/").pop();
  if (isAdmin) await admin(`/users/${id}/groups/${adminsGroup}`, { method: "PUT" });
  return { id, email: `${name}@probe.test`, password };
}
const kinds = async (id) => (await admin(`/users/${id}/credentials`)).map((c) => c.type).sort().join(",");

const browser = await puppeteer.launch({ executablePath: "/usr/bin/google-chrome", headless: true });
function pkce() { const v = randomBytes(32).toString("base64url"); return { v, c: createHash("sha256").update(v).digest("base64url") }; }
// A sign-in through the account console (the realm's browser flow), or temporal-ui's own flow
function authUrl({ client = "account-console", redirect = `${KC}/realms/gen9/account/`, extra = {} } = {}) {
  const { c } = pkce();
  return `${KC}/realms/gen9/protocol/openid-connect/auth?${new URLSearchParams({ client_id: client, redirect_uri: redirect, response_type: "code", scope: "openid", state: randomBytes(8).toString("hex"), code_challenge: c, code_challenge_method: "S256", ...extra })}`;
}
const wait = (page) => page.waitForNavigation({ waitUntil: "networkidle0", timeout: 20000 }).catch(() => {});
async function settled(page) { await page.waitForFunction(() => document.readyState === "complete" && performance.now() > 1500, { polling: 100, timeout: 30000 }).catch(() => {}); }
// What page Keycloak shows: the form's ids tell
async function where(page) {
  const url = page.url();
  if (!url.startsWith(`${KC}/realms/gen9/login-actions`) && !url.includes("/protocol/openid-connect/auth")) return `left Keycloak: ${url.split("?")[0]}${url.includes("code=") ? " (code)" : ""}`;
  if (await page.$("#totpSecret")) return "set up an authenticator app";
  if (await page.$("#otp")) return "authenticator code";
  if (await page.$("#password")) return "password";
  if (await page.$("#kc-page-title, h1")) return `page: ${await page.$eval("#kc-page-title, h1", (h) => h.textContent.trim())}`;
  return `unknown: ${url.split("?")[0]}`;
}
async function password(page, user, url = authUrl()) {
  await page.goto(url, { waitUntil: "networkidle0" });
  await settled(page);
  if (!(await page.$("#password"))) return where(page);
  // Re-authentication (prompt=login, max_age) asks for the password alone
  if (await page.$("#username")) await page.type("#username", user.email);
  await page.type("#password", user.password);
  await Promise.all([wait(page), page.click("#kc-login")]);
  return where(page);
}
async function code(page, secret, counter = step()) {
  await page.type("#otp", totp(secret, counter));
  await Promise.all([wait(page), page.click("#kc-login")]);
  return where(page);
}

try {
  const env2 = env.GEN9_SEED_ADMIN_OTP_SECRET;
  const ada = { email: "ada@gen9.test", password: env.GEN9_SEED_ADMIN_PASSWORD };
  const alan = { email: "alan@gen9.test", password: env.GEN9_SEED_USER_PASSWORD };

  // A. The seeded admin: password, then the code from the secret configure.sh gave
  let ctx = await browser.createBrowserContext();
  let page = await ctx.newPage();
  note("A seeded admin, after the password", await password(page, ada));
  const adaStep = await nextStep(step()); // a window no earlier run used
  note("A seeded admin, after the code from .env's secret", await code(page, env2, adaStep));
  // H. The same code again in another browser: refused?
  const ctx2 = await browser.createBrowserContext();
  const p2 = await ctx2.newPage();
  await password(p2, ada);
  note("H the same code twice (another browser, same window)", await code(p2, env2, adaStep));
  const errorText = await p2.$eval("#input-error-otp-code", (e) => e.textContent.trim()).catch(() => "(no error shown)");
  note("H its error", errorText);
  await ctx2.close();
  // G. Step-up (prompt=login) with a live session: password and code again?
  note("G seeded admin, prompt=login, after the password", await password(page, ada, authUrl({ extra: { prompt: "login" } })));
  const next = await nextStep(adaStep);
  note("G after a fresh code", await code(page, env2, next));
  // G2. max_age=0, as Gen9's step-up asks, a few seconds after the last sign-in
  await new Promise((r) => setTimeout(r, 3000));
  note("G2 seeded admin, max_age=0, after the password", await password(page, ada, authUrl({ extra: { max_age: "0" } })));
  const next2 = await nextStep(next);
  note("G2 after a fresh code", await code(page, env2, next2));
  await ctx.close();

  // B. A member: no second step
  ctx = await browser.createBrowserContext();
  page = await ctx.newPage();
  note("B member, after the password", await password(page, alan));
  await ctx.close();

  // C. A new admin with no second step: sets one up at this sign-in
  const grace = await makeUser("grace", true);
  ctx = await browser.createBrowserContext();
  page = await ctx.newPage();
  note("C new admin without an app, after the password", await password(page, grace));
  const secret = await page.$eval("#totpSecret", (i) => i.value).catch(() => null);
  if (secret) {
    await page.type("#totp", totp(secret));
    await Promise.all([wait(page), page.click('input[type="submit"], button[type="submit"]')]);
    note("C after setting it up", await where(page));
  }
  note("C credentials now", await kinds(grace.id));
  await ctx.close();

  // E. Made admin while signed in: the Keycloak session carries on without a second step?
  const hedy = await makeUser("hedy", false);
  ctx = await browser.createBrowserContext();
  page = await ctx.newPage();
  note("E member, after the password", await password(page, hedy));
  await admin(`/users/${hedy.id}/groups/${adminsGroup}`, { method: "PUT" });
  await page.goto(authUrl(), { waitUntil: "networkidle0" });
  note("E made admin, signing in again with the same session", await where(page));
  await admin(`/users/${hedy.id}/logout`, { method: "POST" });
  note("E signed out by the admin API, then", await password(page, hedy));
  await ctx.close();

  // D. A passkey-only admin: registers a passkey as a member, made admin, then signs in by passkey
  const katherine = await makeUser("katherine", false);
  ctx = await browser.createBrowserContext();
  page = await ctx.newPage();
  const cdp = await page.createCDPSession();
  await cdp.send("WebAuthn.enable");
  await cdp.send("WebAuthn.addVirtualAuthenticator", { options: { protocol: "ctap2", transport: "internal", hasResidentKey: true, hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true } });
  // No autofill: the page waits, so the password path and the button are both ours to choose
  await page.evaluateOnNewDocument(() => { PublicKeyCredential.isConditionalMediationAvailable = async () => false; });
  note("D member, after the password", await password(page, katherine));
  await page.goto(authUrl({ extra: { kc_action: "webauthn-register-passwordless" } }), { waitUntil: "networkidle0" });
  await settled(page);
  note("D register page", await where(page));
  const registerButton = await page.$("#registerWebAuthn, #authenticateWebAuthnButton, button[type=submit]");
  if (registerButton) await Promise.all([wait(page), registerButton.click()]);
  note("D after registering", await where(page));
  note("D credentials", await kinds(katherine.id));
  await admin(`/users/${katherine.id}/groups/${adminsGroup}`, { method: "PUT" });
  await admin(`/users/${katherine.id}/logout`, { method: "POST" });
  await ctx.deleteCookie?.();
  await page.goto(authUrl(), { waitUntil: "networkidle0" });
  await settled(page);
  const passkeyButton = await page.$("#authenticateWebAuthnButton");
  note("D passkey button on the sign-in page", Boolean(passkeyButton));
  if (passkeyButton) await Promise.all([wait(page), passkeyButton.click()]);
  note("D passkey admin, after the passkey", await where(page));
  await admin(`/users/${katherine.id}/logout`, { method: "POST" });
  // D2. The same admin types the password instead
  note("D2 passkey admin with the password instead", await password(page, katherine));
  await ctx.close();

  // F. Temporal UI's flow: a new admin without an app, through the forms
  const mary = await makeUser("mary", true);
  ctx = await browser.createBrowserContext();
  page = await ctx.newPage();
  const tui = authUrl({ client: "temporal-ui", redirect: "http://localhost:25083/auth/sso/callback" }).replace(/&code_challenge=[^&]*&code_challenge_method=S256/, "");
  note("F temporal-ui, new admin without an app, after the password", await password(page, mary, tui));
  // F2. A member there: still refused in Gen9's words, and not asked to set up an app first
  const ctxm = await browser.createBrowserContext();
  const pm = await ctxm.newPage();
  note("F2 temporal-ui, member, after the password", await password(pm, alan, tui));
  await ctxm.close();
  await ctx.close();

  // I. configure.sh again: grace has an app, katherine a passkey, mary none (signed out)
  note("I users", JSON.stringify({ grace: await kinds(grace.id), katherine: await kinds(katherine.id), mary: await kinds(mary.id), hedy: await kinds(hedy.id) }));
} catch (e) {
  note("ERROR", e.stack.split("\n").slice(0, 3).join(" | "));
} finally {
  await browser.close();
}
