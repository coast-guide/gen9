// Helpers for re-running gen9-learn's flows: the same docker commands the guide prints, Keycloak's
// Admin API, Mailpit, and a Chrome DevTools recorder. Nothing here prints a secret: codes, states,
// tokens and cookie values are replaced by "…" before anything is recorded.
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";

export const ROOT = new URL("../../", import.meta.url).pathname;
export const APP = process.env.APP_URL ?? "http://localhost:14000";
export const API = process.env.GEN9_API ?? "http://localhost:17000";
export const KEYCLOAK = process.env.KEYCLOAK_URL ?? "http://localhost:15000";
export const MAILPIT = process.env.MAILPIT_URL ?? "http://localhost:15002";
// Chrome where each platform installs it; CHROME_PATH for anywhere else
export const CHROME =
  process.env.CHROME_PATH ?? (process.platform === "darwin" ? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" : "/usr/bin/google-chrome");

export const readEnv = (file) =>
  existsSync(`${ROOT}${file}`)
    ? Object.fromEntries(
        readFileSync(`${ROOT}${file}`, "utf8")
          .split("\n")
          .filter((line) => /^[A-Z0-9_]+=/.test(line))
          .map((line) => [line.slice(0, line.indexOf("=")), line.slice(line.indexOf("=") + 1)]),
      )
    : {};
export const kcEnv = readEnv("gen9-keycloak/.env");
// Mailpit's API asks for its password (user gen9; MAILPIT_UI_PASSWORD in gen9-keycloak/.env)
const mailAuth = () => ({ headers: { Authorization: `Basic ${Buffer.from(`gen9:${kcEnv.MAILPIT_UI_PASSWORD ?? ""}`).toString("base64")}` } });

let failures = 0;
export function check(ok, what, detail = "") {
  console.log(`${ok ? "ok  " : "FAIL"}  ${what}${detail ? `: ${detail}` : ""}`);
  if (!ok) failures++;
  return ok;
}
export const failed = () => failures;

/** Run a shell command from the repo root (as the guide's commands are), capturing its output. */
export function sh(command, { timeout = 60_000 } = {}) {
  const result = spawnSync("bash", ["-c", command], { cwd: ROOT, encoding: "utf8", timeout });
  return { code: result.status ?? 1, out: `${result.stdout ?? ""}${result.stderr ?? ""}`.trim() };
}
const quote = (s) => `'${s.replaceAll("'", `'\\''`)}'`;
export const appdb = (q) => sh(`docker exec gen9-postgres-postgres-1 psql -U postgres -d gen9_agent -tAc ${quote(q)}`).out;
export const kcdb = (q) => sh(`docker exec gen9-keycloak-postgres-1 psql -U keycloak -d keycloak -tAc ${quote(q)}`).out;
export const ch = (q) => sh(`docker exec gen9-langfuse-clickhouse-1 clickhouse-client -q ${quote(q)}`).out;
export const valkey = (args) =>
  sh(`docker exec gen9-ui-valkey-1 sh -c ${quote(`REDISCLI_AUTH="$VALKEY_PASSWORD" valkey-cli ${args}`)}`).out;

/** Keycloak Admin API as the bootstrap admin (users, sessions, events). */
export async function admin(path, init = {}) {
  // The master realm's admin-cli tokens live 60 s: fetch a new one after 50
  if (!admin.token || Date.now() - admin.at > 50_000) admin.token = null;
  admin.at = admin.token ? admin.at : Date.now();
  admin.token ??= await fetch(`${KEYCLOAK}/realms/master/protocol/openid-connect/token`, {
    method: "POST",
    body: new URLSearchParams({
      grant_type: "password",
      client_id: "admin-cli",
      username: kcEnv.KC_BOOTSTRAP_ADMIN_USERNAME,
      password: kcEnv.KC_BOOTSTRAP_ADMIN_PASSWORD,
    }),
  })
    .then((r) => r.json())
    .then((body) => body.access_token);
  const response = await fetch(`${KEYCLOAK}/admin/realms/gen9${path}`, {
    ...init,
    headers: { Authorization: `Bearer ${admin.token}`, "Content-Type": "application/json" },
  });
  if (!response.ok) throw new Error(`Keycloak ${init.method ?? "GET"} ${path}: ${response.status}`);
  return response.status === 201 || response.status === 204 ? null : response.json();
}

/** The newest email to `to` since `since` (ms), with the links in its text part, and the text itself
 * (for checks only: a verification or reset email's links are one-time secrets, never recorded). */
export async function mailTo(to, since, subject = /./) {
  for (let i = 0; i < 30; i++) {
    const found = await fetch(`${MAILPIT}/api/v1/search?query=${encodeURIComponent(`to:"${to}"`)}`, mailAuth()).then((r) => r.json());
    const message = found.messages?.find((m) => Date.parse(m.Created) >= since - 5000 && subject.test(m.Subject));
    if (message) {
      const full = await fetch(`${MAILPIT}/api/v1/message/${message.ID}`, mailAuth()).then((r) => r.json());
      return { subject: full.Subject, links: full.Text.match(/https?:\/\/\S+/g) ?? [], text: full.Text };
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  return null;
}

// Values that are secrets or one-time (codes, states, tokens, session ids) never reach a recording
const SECRET_PARAMS = /([?&](?:code|state|nonce|code_challenge|session_code|execution|tab_id|client_data|key|token|id_token_hint|session_state|iss)=)[^&#\s]+/g;
export const sanitizeUrl = (url) => url.replace(SECRET_PARAMS, "$1…");

/**
 * Records what Chrome DevTools would show for a page: every request (method, sanitized url, type,
 * status, redirect target), the cookies each response sets (name and attributes, never the value),
 * selected response headers, and console messages. `mark(label)` splits the log into steps.
 */
export async function recorder(page) {
  const cdp = await page.createCDPSession();
  await cdp.send("Network.enable");
  const log = [];
  const byId = new Map();
  let step = "start";
  const entry = (id) => byId.get(id);
  cdp.on("Network.requestWillBeSent", (e) => {
    if (e.redirectResponse) {
      const previous = entry(e.requestId);
      if (previous) {
        previous.status = e.redirectResponse.status;
        previous.location = sanitizeUrl(e.redirectResponse.headers.location ?? e.redirectResponse.headers.Location ?? "");
      }
    }
    const item = {
      id: e.requestId,
      step,
      method: e.request.method,
      url: sanitizeUrl(e.request.url),
      type: e.type,
      nextAction: Object.keys(e.request.headers).some((h) => h.toLowerCase() === "next-action") ? "yes" : undefined,
      accept: e.request.headers.Accept,
    };
    byId.set(e.requestId, item);
    log.push(item);
  });
  cdp.on("Network.responseReceived", (e) => {
    const item = entry(e.requestId);
    if (!item) return;
    item.status = e.response.status;
    item.contentType = e.response.headers["content-type"] ?? e.response.headers["Content-Type"];
    const csp = e.response.headers["content-security-policy"];
    if (csp) item.csp = csp.split(";")[0] + "; …";
  });
  // One request id covers a whole redirect chain: attach headers to the hop with the same status
  // that has none yet (extra info can arrive after the next hop started)
  const hopsOf = (id) => log.filter((item) => item.id === id);
  cdp.on("Network.responseReceivedExtraInfo", (e) => {
    const raw = e.headers["set-cookie"] ?? e.headers["Set-Cookie"];
    if (!raw) return;
    const item = hopsOf(e.requestId).find((hop) => hop.status === e.statusCode && !hop.setCookies) ?? entry(e.requestId);
    if (!item) return;
    item.setCookies = raw.split("\n").map((line) => {
      const [pair, ...attributes] = line.split(";").map((part) => part.trim());
      return `${pair.split("=")[0]}=… ${attributes.join("; ")}`;
    });
  });
  const consoleMessages = [];
  page.on("console", (message) => consoleMessages.push({ step, type: message.type(), text: message.text().slice(0, 200) }));
  page.on("pageerror", (error) => consoleMessages.push({ step, type: "pageerror", text: String(error).slice(0, 200) }));
  return {
    cdp,
    /** The body of a finished request, as DevTools' Response tab shows it */
    body: async (id) => (await cdp.send("Network.getResponseBody", { requestId: id })).body,
    mark: (label) => (step = label),
    log,
    consoleMessages,
    /** Requests of one step that DevTools' Network tab shows by default (documents, fetch, XHR, event streams) */
    hops: (label, types = ["Document", "Fetch", "XHR", "EventSource"]) =>
      log.filter((item) => item.step === label && types.includes(item.type)),
  };
}

// ---- sign-in helpers (the verify run types the throwaway user's password, like make e2e does) ----
import { createHmac } from "node:crypto";

/** The realm's authenticator-code policy (period, digits, algorithm). */
export async function otpPolicy() {
  const realm = await admin("");
  return { period: realm.otpPolicyPeriod, digits: realm.otpPolicyDigits, algorithm: realm.otpPolicyAlgorithm.replace("Hmac", "").toLowerCase() };
}
/** RFC 6238 code; Keycloak keys the HMAC with the secret's raw bytes (the setup page's #totpSecret). */
export function totp(secret, policy, counter = Math.floor(Date.now() / 1000 / policy.period)) {
  const message = Buffer.alloc(8);
  message.writeBigUInt64BE(BigInt(counter));
  const hash = createHmac(policy.algorithm, Buffer.from(secret, "utf8")).update(message).digest();
  const offset = hash[hash.length - 1] & 0xf;
  return String((hash.readUInt32BE(offset) & 0x7fffffff) % 10 ** policy.digits).padStart(policy.digits, "0");
}
/** Keycloak refuses a code twice: wait for a code window after `counter`. */
export async function nextWindow(policy, counter) {
  while (Math.floor(Date.now() / 1000 / policy.period) <= counter) await new Promise((resolve) => setTimeout(resolve, 500));
  return Math.floor(Date.now() / 1000 / policy.period);
}
/**
 * Keycloak's pages run authChecker.js: about 1 s after a page loads, it reloads the page if the
 * KC_AUTH_SESSION_HASH cookie no longer matches it (another flow in this browser changed it), and
 * whatever was typed before is lost (keycloak/keycloak#34652 shows the same check). Wait until the
 * page has outlived that check, and say when it reloaded.
 */
export async function settled(page) {
  if (!page.url().startsWith(KEYCLOAK)) return;
  let loads = 0;
  const onNavigated = (frame) => frame === page.mainFrame() && loads++;
  page.on("framenavigated", onNavigated);
  try {
    await page.waitForFunction(() => document.readyState === "complete" && performance.now() > 1500, { polling: 100, timeout: 30_000 });
  } finally {
    page.off("framenavigated", onNavigated);
  }
  if (loads) console.log(`      (a Keycloak page loaded again ${loads}x before it settled, now ${new URL(page.url()).pathname})`);
}
export const navigation = async (page, action) => {
  await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0", timeout: 20_000 }), action]);
  await settled(page);
};
/** Password on Keycloak's sign-in page; the caller handles any second step. */
export async function signInWithPassword(page, email, password) {
  await page.goto(`${APP}/auth/login`, { waitUntil: "networkidle0" });
  await settled(page);
  await page.type("#username", email);
  await page.type("#password", password);
  await navigation(page, page.click("#kc-login"));
}
export async function signOut(page) {
  if (!page.url().startsWith(APP)) await page.goto(`${APP}/chat`, { waitUntil: "networkidle0" });
  await navigation(page, page.evaluate(() => document.querySelector('form[action="/auth/logout"]').requestSubmit()));
}
