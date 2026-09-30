// Connectors that need sign-in (milestone 2, MCP authorization), as the seeded user, in Chrome, with a
// test MCP server that is its own authorization server (fixtures/oauth_mcp.py: FastMCP's in-memory
// OAuth provider, which approves every sign-in; access tokens last 30 s):
//   1. adding it in Settings sends the browser to sign in, and back: "Signed in", its tool listed;
//      the tokens are sealed in Postgres; a replayed or unknown state is refused
//   2. a chat uses its tool after the access token has expired (gen9-agent refreshed it)
//   3. with every token revoked at the server, the next run's refresh fails: the connector asks to
//      Reconnect (no serious accessibility violations), and Reconnect signs in again
//   4. Remove disconnects it, and its tokens are revoked at the server (RFC 7009)
//   5. a throwaway account signs in to it (through the API, as the web app would), then deletes
//      itself: DeleteAccountWorkflow revokes the connector's tokens at the server too
// Needs gen9-agent to allow the test server: CONNECTORS_ALLOWED_HOSTS=["host.docker.internal:17801"]
// in gen9-agent/.env. The check starts the server itself (uv, fastmcp 4.0.9) and stops it at the end.
import { execFileSync, spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { bypassCSP, injectAxe, launch } from "./browser.mjs";
import { ROOT, signInTerminal } from "./signin.mjs";

const env = Object.fromEntries(
  readFileSync(`${ROOT}gen9-keycloak/.env`, "utf8")
    .split("\n")
    .filter((line) => /^[A-Z0-9_]+=/.test(line))
    .map((line) => [line.slice(0, line.indexOf("=")), line.slice(line.indexOf("=") + 1)]),
);
const APP = process.env.APP_URL ?? "http://localhost:14000";
const KEYCLOAK = process.env.KEYCLOAK_URL ?? "http://localhost:15000";
const API = process.env.GEN9_API ?? "http://localhost:17000";
const AXE = readFileSync(createRequire(import.meta.url).resolve("axe-core/axe.min.js"), "utf8");
const PORT = 17801;
// One address for gen9-agent's containers (Docker Desktop) and this check's Chrome (mapped below)
const SERVER = `http://host.docker.internal:${PORT}/mcp`;
const SELECT = 'select[aria-label="When Gen9 asks before using notes"]';

let failures = 0;
function check(ok, what, detail = "") {
  console.log(`${ok ? "ok  " : "FAIL"}  ${what}${detail ? `: ${detail}` : ""}`);
  if (!ok) failures++;
  return ok;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const gen9 = (configDir, args) =>
  new Promise((resolve) => {
    const child = spawn("uv", ["run", "-q", "gen9", ...args], { cwd: `${ROOT}gen9-cli`, env: { ...process.env, GEN9_CONFIG_DIR: configDir } });
    let out = "";
    child.stdout.on("data", (d) => (out += d));
    child.stderr.on("data", (d) => (out += d));
    child.on("exit", (code) => resolve({ code, out }));
  });
async function api(configDir, method, path, body) {
  await gen9(configDir, ["whoami"]);
  const token = JSON.parse(readFileSync(join(configDir, "credentials.json"), "utf8")).access_token;
  const response = await fetch(`${API}${path}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  return { status: response.status, body: response.status === 204 ? null : await response.json().catch(() => null) };
}
// Keycloak's Admin API as the bootstrap admin, for the throwaway account (a fresh token each time)
async function admin(path, init = {}) {
  const token = await fetch(`${KEYCLOAK}/realms/master/protocol/openid-connect/token`, {
    method: "POST",
    body: new URLSearchParams({ grant_type: "password", client_id: "admin-cli", username: env.KC_BOOTSTRAP_ADMIN_USERNAME, password: env.KC_BOOTSTRAP_ADMIN_PASSWORD }),
  })
    .then((r) => r.json())
    .then((body) => body.access_token);
  const response = await fetch(`${KEYCLOAK}/admin/realms/gen9${path}`, { ...init, headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" } });
  if (!response.ok && response.status !== 404) throw new Error(`Keycloak ${init.method ?? "GET"} ${path}: ${response.status}`);
  return [201, 204, 404].includes(response.status) ? null : response.json();
}
const revokedAtServer = () => fetch(`http://127.0.0.1:${PORT}/test/revoked`).then((r) => r.json());
const psql = (sql) => execFileSync("docker", ["exec", "gen9-postgres-postgres-1", "psql", "-U", "postgres", "-d", "gen9_agent", "-tAc", sql], { encoding: "utf8" }).trim();
async function buttonWithText(page, scope, text) {
  for (const b of await page.$$(`${scope} button`)) if ((await b.evaluate((e) => e.textContent.trim())) === text) return b;
  throw new Error(`no ${text} button in ${scope}`);
}
async function ask(page, text) {
  await page.goto(`${APP}/chat`, { waitUntil: "networkidle0" });
  await page.type("#composer", text);
  await page.keyboard.press("Enter");
  await page.waitForSelector('button[aria-label="Stop"]', { timeout: 30_000 }).catch(() => {});
  await page.waitForFunction(() => !document.querySelector('button[aria-label="Stop"]'), { timeout: 240_000, polling: 500 });
  const id = page.url().match(/\/chat\/([0-9a-f-]{36})/)?.[1];
  const answer = await page.$$eval("ol[aria-live] > li", (lis) => lis.at(-1)?.textContent ?? "");
  return { id, answer };
}

// The test server, unless one already runs on the port
const server = await fetch(`http://127.0.0.1:${PORT}/.well-known/oauth-authorization-server`).then(() => null, () =>
  // Its own process group, so stopping it stops uv's child too
  spawn("uv", ["run", "-q", "--with", "fastmcp==4.0.9", "python", "e2e/fixtures/oauth_mcp.py", String(PORT)], { cwd: ROOT, stdio: "ignore", detached: true }),
);
for (let i = 0; i < 60; i++) {
  if (await fetch(`http://127.0.0.1:${PORT}/.well-known/oauth-authorization-server`).then((r) => r.ok, () => false)) break;
  await sleep(500);
}

const alan = mkdtempSync(join(tmpdir(), "gen9-oauth-"));
const throwaway = mkdtempSync(join(tmpdir(), "gen9-oauth-throwaway-"));
const chats = [];
let throwawayId;
try {
  check(Boolean(await signInTerminal({ email: env.GEN9_SEED_USER_EMAIL, password: env.GEN9_SEED_USER_PASSWORD, configDir: alan })), "the seeded user signs in on the terminal");
  for (const c of (await api(alan, "GET", "/v1/me/connectors")).body ?? []) if (c.name === "notes") await api(alan, "DELETE", `/v1/me/connectors/${c.id}`);
  // gen9-agent must allow the test server; say how if it doesn't
  const allowed = readFileSync(`${ROOT}gen9-agent/.env`, "utf8").includes(`host.docker.internal:${PORT}`);
  if (!check(allowed, "gen9-agent allows the test server", allowed ? "" : `add CONNECTORS_ALLOWED_HOSTS=["host.docker.internal:${PORT}"] to gen9-agent/.env (make setup does), then make up STACKS=agent`)) {
    throw new Error("gen9-agent doesn't allow the test server");
  }

  const browser = await launch({
    headless: !process.env.HEADED,
    defaultViewport: { width: 1280, height: 900 },
    args: ["--host-resolver-rules=MAP host.docker.internal 127.0.0.1"],
  });
  try {
    const page = await browser.newPage();
    await bypassCSP(page); // for the injected axe script
    await page.goto(`${APP}/auth/login?returnTo=/settings`, { waitUntil: "networkidle0" });
    await page.type("#username", env.GEN9_SEED_USER_EMAIL);
    await page.type("#password", env.GEN9_SEED_USER_PASSWORD);
    await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }), page.click("#kc-login")]);
    if (!page.url().includes("/settings")) await page.goto(`${APP}/settings`, { waitUntil: "networkidle0" });

    // 1. Add it: off to sign in, and back
    await (await buttonWithText(page, "main", "Add a connector")).click();
    const form = 'form[aria-label="Add a connector"]';
    await page.waitForSelector(form);
    await page.type(`${form} input:not([type=url]):not([type=password])`, "notes");
    await page.type(`${form} input[type=url]`, SERVER);
    await Promise.all([page.waitForNavigation({ waitUntil: "load", timeout: 60_000 }), (await buttonWithText(page, form, "Add")).click()]);
    await page.waitForFunction(() => location.pathname === "/settings" && new URLSearchParams(location.search).has("sign_in"), { timeout: 60_000 });
    const notice = await page.$eval('[role="status"], [role="alert"]', (n) => n.textContent).catch(() => "");
    const row = await page.evaluate((select) => document.querySelector(select)?.closest("div.grid")?.textContent ?? "", SELECT);
    check(notice.includes("Signed in") && /1 tool/.test(row) && !row.includes("Reconnect"), "adding it sends you to sign in and back: signed in, its tool listed", `${notice}; ${row.slice(0, 80)}`);
    const sealed = psql("select coalesce(sealed_tokens, '') from connectors where name = 'notes'");
    // "<key id>:<base64>" (vault.py); an id is any name without a colon, such as k3b
    check(/^[^:\s]+:[A-Za-z0-9+/]+=*$/.test(sealed) && !sealed.includes("test_auth") && !sealed.includes("access_token"), "its tokens are sealed in Postgres", sealed.slice(0, 12));
    const replay = await api(alan, "POST", "/v1/me/connectors/sign-in/callback", { code: "x", state: "not-a-state-under-way-at-all" });
    check(replay.status === 400, "an unknown or replayed state is refused", `HTTP ${replay.status}`);

    // 2. A chat uses it after the access token has expired
    await page.select(SELECT, "never");
    await page.waitForNetworkIdle({ idleTime: 500 });
    await sleep(35_000);
    const used = await ask(page, "Use your notes connector's secret_note tool and tell me exactly what the note says.");
    chats.push(used.id);
    check(/lighthouse keepers log the weather at dawn/i.test(used.answer), "a chat uses its tool once the access token has expired (refreshed)", used.answer.slice(0, 90));

    // 3. Every token revoked at the server: the next run asks to Reconnect
    await fetch(`http://127.0.0.1:${PORT}/test/revoke`, { method: "POST" });
    const lapsed = await ask(page, "Use your notes connector's secret_note tool if you have it; if you don't, say: NO NOTES TOOL.");
    chats.push(lapsed.id);
    await page.goto(`${APP}/settings`, { waitUntil: "networkidle0" });
    const lapsedRow = await page.evaluate((select) => document.querySelector(select)?.closest("div.grid")?.textContent ?? "", SELECT);
    check(lapsedRow.includes("sign-in has lapsed") && lapsedRow.includes("Reconnect") && psql("select status from connectors where name = 'notes'") === "reconnect", "with its tokens revoked, it asks to Reconnect", lapsedRow.slice(0, 90));
    await injectAxe(page, AXE);
    const { violations } = await page.evaluate(() => window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] } }));
    const blocking = violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    check(!blocking.length, "Settings asking to Reconnect has no serious accessibility violations", blocking.map((v) => v.id).join(", "));
    await Promise.all([page.waitForNavigation({ waitUntil: "load", timeout: 60_000 }), (await buttonWithText(page, "main", "Reconnect")).click()]);
    await page.waitForFunction(() => location.pathname === "/settings" && new URLSearchParams(location.search).get("sign_in") === "done", { timeout: 60_000 });
    check(psql("select status from connectors where name = 'notes'") === "ready", "Reconnect signs in again");

    // 4. Remove it
    const select = await page.$(SELECT);
    const remove = await select.evaluateHandle((s) => [...s.parentElement.querySelectorAll("button")].find((b) => b.textContent.trim() === "Remove"));
    await remove.click();
    await page.waitForSelector('[role="alertdialog"]');
    await (await buttonWithText(page, '[role="alertdialog"]', "Remove")).click();
    await page.waitForFunction((s) => !document.querySelector(s), { timeout: 30_000 }, SELECT);
    check(psql("select count(*) from connectors where name = 'notes'") === "0", "Remove disconnects it, and its tokens go with it");
    const revoked = await fetch(`http://127.0.0.1:${PORT}/test/revoked`).then((r) => r.json());
    check(
      revoked.revoked.includes("RefreshToken") && revoked.refresh_tokens_left === 0 && revoked.access_tokens_left === 0,
      "its tokens are revoked at the server (RFC 7009)",
      `revoked ${revoked.revoked.join(", ") || "nothing"}; left: ${revoked.refresh_tokens_left} refresh, ${revoked.access_tokens_left} access`,
    );

    await page.evaluate(() => document.querySelector('form[action="/auth/logout"]')?.requestSubmit()).catch(() => {});
    await sleep(1500);
  } finally {
    await browser.close();
  }

  // 5. A throwaway account signs in to it, then deletes itself
  const email = `oauth-${Date.now()}@gen9.test`;
  const password = `e2e-${randomBytes(12).toString("hex")}`;
  await admin("/users", {
    method: "POST",
    body: JSON.stringify({ username: email, email, firstName: "OAuth", lastName: "Check", enabled: true, emailVerified: true, credentials: [{ type: "password", value: password, temporary: false }] }),
  });
  throwawayId = (await admin(`/users?email=${encodeURIComponent(email)}&exact=true`))[0].id;
  check(Boolean(await signInTerminal({ email, password, configDir: throwaway })), "a throwaway account signs in on the terminal");
  const started = await api(throwaway, "POST", "/v1/me/connectors", { name: "notes", url: SERVER, policy: "never" });
  // The test server approves every sign-in: its redirect carries what the web app's callback would hand on
  const approved = await fetch(started.body.authorize_url.replace("host.docker.internal", "127.0.0.1"), { redirect: "manual" });
  const back = new URL(approved.headers.get("location"));
  const finished = await api(throwaway, "POST", "/v1/me/connectors/sign-in/callback", {
    code: back.searchParams.get("code"),
    state: back.searchParams.get("state"),
    ...(back.searchParams.get("iss") ? { iss: back.searchParams.get("iss") } : {}),
  });
  const held = await revokedAtServer();
  check(finished.status === 200 && finished.body?.status === "ready" && held.refresh_tokens_left === 1, "it signs in to the test server through the API", `HTTP ${finished.status}; ${held.refresh_tokens_left} refresh token held`);
  const deleted = await api(throwaway, "DELETE", "/v1/me");
  let after = held;
  for (let i = 0; i < 60 && after.refresh_tokens_left !== 0; i++) {
    await sleep(1000);
    after = await revokedAtServer();
  }
  check(
    [202, 204].includes(deleted.status) && after.revoked.length > held.revoked.length && after.refresh_tokens_left === 0 && after.access_tokens_left === 0,
    "deleting the account revokes its connector's tokens at the server",
    `DELETE /v1/me ${deleted.status}; revoked ${after.revoked.length - held.revoked.length} more; left: ${after.refresh_tokens_left} refresh, ${after.access_tokens_left} access`,
  );
} catch (e) {
  check(false, "the sign-in check ran to the end", e.message);
} finally {
  if (throwawayId) await admin(`/users/${throwawayId}`, { method: "DELETE" }).catch(() => {});
  rmSync(throwaway, { recursive: true, force: true });
  for (const c of (await api(alan, "GET", "/v1/me/connectors").catch(() => ({ body: [] }))).body ?? []) {
    if (c.name === "notes") await api(alan, "DELETE", `/v1/me/connectors/${c.id}`).catch(() => {});
  }
  for (const chat of chats.filter(Boolean)) await api(alan, "DELETE", `/v1/threads/${chat}`).catch(() => {});
  await gen9(alan, ["logout"]).catch(() => {});
  rmSync(alan, { recursive: true, force: true });
  if (server) process.kill(-server.pid);
}

console.log(failures ? `\n${failures} check(s) failed` : "\nall checks passed");
process.exit(failures ? 1 : 0);
