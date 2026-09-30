// What an admin does, and the limits that keep one person from holding without end (M9, item 7:
// what no check covered). An admin acts in the web app, never the terminal (auth-architecture.md,
// decision 11), so Ada works in Chrome, her password typed by Puppeteer; throwaway people are made
// through Keycloak's Admin API, each signed in on a terminal of their own (signin.mjs):
//   1. a password reset from Users: the toast, the email in Mailpit, the audit record
//   2. an admin deletes a person from Users: the toast, then gone from Keycloak and Gen9, recorded
//   3. a person's caps: the 11th scheduled task, the 101st environment secret and the 51st connector
//      are refused (409); connectors use e2e's elicitation test server (fixtures/elicit_mcp.py)
//   4. step-up for a client that isn't the web app: a terminal sign-in over 5 minutes old can't
//      delete the account (401, insufficient_user_authentication, max_age 300); a fresh one can
// Ada gets no chat from any of it; every throwaway person is deleted at the end.
import { execFileSync, spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { launch } from "./browser.mjs";
import { ROOT, signInTerminal } from "./signin.mjs";

const env = Object.fromEntries(
  readFileSync(`${ROOT}gen9-keycloak/.env`, "utf8")
    .split("\n")
    .filter((line) => /^[A-Z0-9_]+=/.test(line))
    .map((line) => [line.slice(0, line.indexOf("=")), line.slice(line.indexOf("=") + 1)]),
);
const API = process.env.GEN9_API ?? "http://localhost:17000";
const KEYCLOAK = process.env.KEYCLOAK_URL ?? "http://localhost:15000";
const MAILPIT = process.env.MAILPIT_URL ?? "http://localhost:15002";
const APP = process.env.APP_URL ?? "http://localhost:14000";
const STEP_UP_S = 300;

let failures = 0;
function check(ok, what, detail = "") {
  console.log(`${ok ? "ok  " : "FAIL"}  ${what}${detail ? `: ${detail}` : ""}`);
  if (!ok) failures++;
  return ok;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const psql = (query) => execFileSync("docker", ["exec", "gen9-postgres-postgres-1", "psql", "-U", "postgres", "-d", "gen9_agent", "-tAc", query], { encoding: "utf8" }).trim();
async function keycloak(path, init = {}) {
  const token = await fetch(`${KEYCLOAK}/realms/master/protocol/openid-connect/token`, {
    method: "POST",
    body: new URLSearchParams({ grant_type: "password", client_id: "admin-cli", username: env.KC_BOOTSTRAP_ADMIN_USERNAME, password: env.KC_BOOTSTRAP_ADMIN_PASSWORD }),
  }).then((r) => r.json()).then((b) => b.access_token);
  const response = await fetch(`${KEYCLOAK}/admin/realms/gen9${path}`, { ...init, headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" } });
  if (!response.ok && response.status !== 404) throw new Error(`Keycloak ${init.method ?? "GET"} ${path}: ${response.status}`);
  return [201, 204, 404].includes(response.status) ? null : response.json();
}
const gen9 = (dir, args) => execFileSync("uv", ["run", "-q", "gen9", ...args], { cwd: `${ROOT}gen9-cli`, env: { ...process.env, GEN9_CONFIG_DIR: dir }, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
const refreshed = new Map();
async function api(dir, method, path, body) {
  // The terminal refreshes its tokens (they live 5 minutes): at most once a minute here
  if (Date.now() - (refreshed.get(dir) ?? 0) > 60_000) {
    gen9(dir, ["whoami"]);
    refreshed.set(dir, Date.now());
  }
  const token = JSON.parse(readFileSync(join(dir, "credentials.json"), "utf8")).access_token;
  const response = await fetch(`${API}${path}`, { method, headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  return { status: response.status, headers: response.headers, body: await response.json().catch(() => null) };
}
const mails = async (to, subject) => {
  const auth = { headers: { Authorization: `Basic ${Buffer.from(`gen9:${env.MAILPIT_UI_PASSWORD ?? ""}`).toString("base64")}` } };
  const found = await fetch(`${MAILPIT}/api/v1/search?query=${encodeURIComponent(`to:"${to}"`)}`, auth).then((r) => r.json());
  return (found.messages ?? []).filter((m) => subject.test(m.Subject));
};

// A throwaway person, signed in on a terminal of their own (their Gen9 row is made by that first call)
const stamp = Date.now();
const people = [];
async function person(tag) {
  const email = `admin-api-${tag}-${stamp}@gen9.test`;
  const password = `${randomBytes(18).toString("base64url")}Aa1!`;
  await keycloak("/users", { method: "POST", body: JSON.stringify({ username: email, email, emailVerified: true, enabled: true, firstName: "Admin", lastName: `API ${tag}`, credentials: [{ type: "password", value: password, temporary: false }] }) });
  const sub = (await keycloak(`/users?email=${encodeURIComponent(email)}&exact=true`))[0].id;
  const dir = mkdtempSync(join(tmpdir(), "gen9-admin-api-"));
  await signInTerminal({ email, password, configDir: dir });
  await api(dir, "GET", "/v1/me");
  const one = { email, password, sub, dir, signedInAt: Date.now() };
  people.push(one);
  return one;
}
const until = async (what, ok, ms = 60_000) => {
  for (const end = Date.now() + ms; Date.now() < end; await sleep(1000)) if (await ok()) return true;
  return false;
};

const browser = await launch({ headless: !process.env.HEADED, defaultViewport: { width: 1280, height: 900 } });
try {
  const stale = await person("stale"); // signed in first: its sign-in is over 5 minutes old by step 4
  const reset = await person("reset");
  const removed = await person("removed");

  const ada = await browser.newPage();
  await ada.goto(`${APP}/auth/login?returnTo=/admin/users`, { waitUntil: "networkidle0" });
  await ada.type("#username", env.GEN9_SEED_ADMIN_EMAIL);
  await ada.type("#password", env.GEN9_SEED_ADMIN_PASSWORD);
  await Promise.all([ada.waitForNavigation({ waitUntil: "networkidle0" }), ada.click("#kc-login")]);
  const toast = (pattern) => ada.waitForFunction((source) => [...document.querySelectorAll("[data-sonner-toast]")].map((t) => t.textContent.trim()).find((t) => new RegExp(source).test(t)) || false, { timeout: 60_000 }, pattern.source).then((h) => h.jsonValue(), () => "");

  // 1. A password reset, from the person's row
  const since = Date.now();
  await ada.goto(`${APP}/admin/users?q=${encodeURIComponent(reset.email)}`, { waitUntil: "networkidle0" });
  await ada.click(`button[aria-label="Actions for ${reset.email}"]`);
  await ada.waitForSelector("[role=menuitem]");
  for (const item of await ada.$$("[role=menuitem]")) if ((await item.evaluate((el) => el.textContent.trim())) === "Send password reset") await item.click();
  const sentToast = await toast(/^Password reset email sent\.$/);
  const arrived = await until("the email", async () => (await mails(reset.email, /Update your Gen9 account/)).some((m) => Date.parse(m.Created) >= since - 5000));
  const recorded = psql(`select count(*) from audit_events where action = 'admin.user.password_reset' and target = '${reset.sub}'`);
  check(Boolean(sentToast) && arrived && recorded === "1", "a password reset from Users: the toast, the email reaches the person, and the audit record has it", `${sentToast}; email ${arrived}; audit ${recorded}`);

  // 2. An admin deletes a person, from the person's row (its dialog asks for their email first)
  await ada.goto(`${APP}/admin/users?q=${encodeURIComponent(removed.email)}&delete=${removed.sub}`, { waitUntil: "networkidle0" });
  await ada.waitForSelector(`#delete-${removed.sub}`);
  await ada.type(`#delete-${removed.sub}`, removed.email);
  for (const b of await ada.$$("[role=alertdialog] button")) if ((await b.evaluate((el) => el.textContent.trim())) === "Delete user") await b.click();
  const deletedToast = await toast(/^(User deleted with all their data\.|Deleting the user)/);
  const removedGone = await until("the deleted person", async () => psql(`select count(*) from users where sub = '${removed.sub}'`) === "0" && (await keycloak(`/users/${removed.sub}`)) === null, 120_000);
  const audited = psql(`select count(*) from audit_events where action = 'admin.user.delete' and target = '${removed.sub}'`);
  check(Boolean(deletedToast) && removedGone && audited === "1", "an admin deletes a person from Users: the toast, then gone from Keycloak and Gen9, and recorded", `${deletedToast}; gone ${removedGone}; audit ${audited}`);
  await ada.evaluate(() => document.querySelector('form[action="/auth/logout"]')?.requestSubmit()).catch(() => {});

  // 3. A person's caps
  const tasks = [];
  let lastTask;
  for (let i = 0; i < 11; i++) {
    lastTask = await api(stale.dir, "POST", "/v1/tasks", { name: `Cap ${i + 1}`, prompt: "Reply with one word: cap", schedule: { kind: "once", time: "09:00", date: "2027-01-01" }, time_zone: "UTC" });
    if (lastTask.status === 201) tasks.push(lastTask.body.id);
  }
  check(tasks.length === 10 && lastTask.status === 409, "the 11th scheduled task is refused (409), the first 10 kept", `${tasks.length} kept; the 11th ${lastTask.status} ${JSON.stringify(lastTask.body?.detail ?? "")}`);
  for (const id of tasks) await api(stale.dir, "DELETE", `/v1/tasks/${id}`);
  const secrets = [];
  let lastSecret;
  for (let i = 0; i < 101; i++) {
    lastSecret = await api(stale.dir, "POST", "/v1/me/environment-secrets", { name: `cap${i + 1}`, host: `cap${i + 1}.example.com`, value: "not-a-real-secret" });
    if (lastSecret.status === 201) secrets.push(lastSecret.body.id);
  }
  check(secrets.length === 100 && lastSecret.status === 409, "the 101st environment secret is refused (409), the first 100 kept", `${secrets.length} kept; the 101st ${lastSecret.status} ${JSON.stringify(lastSecret.body?.detail ?? "")}`);
  for (const id of secrets) await api(stale.dir, "DELETE", `/v1/me/environment-secrets/${id}`);
  const PORT = 17802;
  const up = () => fetch(`http://127.0.0.1:${PORT}/mcp`, { method: "POST" }).then(() => true, () => false);
  const server = (await up()) ? null : spawn("uv", ["run", "-q", "--with", "fastmcp==4.0.9", "python", "e2e/fixtures/elicit_mcp.py", String(PORT)], { cwd: ROOT, stdio: "ignore", detached: true });
  for (let i = 0; i < 60 && !(await up()); i++) await sleep(500);
  const connectors = [];
  let lastConnector;
  try {
    for (let i = 0; i < 51; i++) {
      lastConnector = await api(stale.dir, "POST", "/v1/me/connectors", { name: `cap${i + 1}`, url: `http://host.docker.internal:${PORT}/mcp`, policy: "never" });
      if (lastConnector.status === 201) connectors.push(lastConnector.body.id);
    }
  } finally {
    for (const id of connectors) await api(stale.dir, "DELETE", `/v1/me/connectors/${id}`);
    if (server) process.kill(-server.pid);
  }
  check(connectors.length === 50 && lastConnector.status === 409, "the 51st connector is refused (409), the first 50 kept", `${connectors.length} kept; the 51st ${lastConnector.status} ${JSON.stringify(lastConnector.body?.detail ?? "")}`);

  // 4. Step-up for a client that isn't the web app
  const wait = stale.signedInAt + (STEP_UP_S + 20) * 1000 - Date.now();
  if (wait > 0) {
    console.log(`      (waiting ${Math.round(wait / 1000)} s, until that terminal's sign-in is over 5 minutes old)`);
    await sleep(wait);
  }
  const old = await api(stale.dir, "DELETE", "/v1/me");
  const challenge = old.headers.get("www-authenticate") ?? "";
  const still = psql(`select count(*) from users where sub = '${stale.sub}'`);
  await signInTerminal({ email: stale.email, password: stale.password, configDir: stale.dir });
  refreshed.delete(stale.dir);
  const fresh = await api(stale.dir, "DELETE", "/v1/me");
  check(old.status === 401 && /insufficient_user_authentication/.test(challenge) && /max_age="?300/.test(challenge) && still === "1" && [202, 204].includes(fresh.status),
    "a terminal sign-in over 5 minutes old can't delete the account (401, insufficient_user_authentication, max_age 300); a fresh sign-in can", `${old.status} ${challenge.slice(0, 110)}; kept ${still}; fresh ${fresh.status}`);
} catch (e) {
  check(false, "the admin API check ran to the end", e.message);
} finally {
  for (const p of people) {
    await keycloak(`/users/${p.sub}`, { method: "DELETE" }).catch(() => {});
    rmSync(p.dir, { recursive: true, force: true });
  }
  await browser.close();
}

console.log(failures ? `\n${failures} check(s) failed` : "\nall checks passed");
process.exit(failures ? 1 : 0);
