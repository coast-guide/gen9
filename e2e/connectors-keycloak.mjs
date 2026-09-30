// Keycloak as a connector's authorization server (milestone 2): an MCP server that trusts a Keycloak
// realm, as an organisation's internal servers would. The check makes a throwaway realm on
// gen9-keycloak and a test server for it (fixtures/keycloak_mcp.py: FastMCP's KeycloakAuthProvider),
// and deletes both at the end. The realm:
//   - its frontend URL http://host.docker.internal:15000, so its issuer is one address for
//     gen9-agent's containers (Docker Desktop) and this check's Chrome (a host-resolver rule);
//   - a default client scope whose Audience mapper names the test server, since Keycloak ignores
//     RFC 8707 `resource` (keycloak.org, "Integrating with MCP");
//   - anonymous Dynamic Client Registration allowed from Gen9 (its Trusted Hosts policy removed),
//     and a test user.
// Then, as the seeded user in Chrome:
//   1. Settings > Add a connector: Gen9 registers by DCR, the browser goes to the realm's sign-in;
//      the test user signs in (and consents); back to "Signed in", its tool listed
//   2. a chat uses its tool with the token Keycloak issued for the server's audience
//   3. Remove: the tokens are revoked at Keycloak (RFC 7009), which ends the client's offline session
// Needs gen9-agent to allow host.docker.internal:17804 and host.docker.internal:15000
// (CONNECTORS_ALLOWED_HOSTS; make setup does).
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
const APP = process.env.APP_URL ?? "http://localhost:14000";
const API = process.env.GEN9_API ?? "http://localhost:17000";
const KEYCLOAK = process.env.KEYCLOAK_URL ?? "http://localhost:15000";
const PORT = 17804;
const SERVER = `http://host.docker.internal:${PORT}/mcp`;
const REALM = `gen9-e2e-mcp-${Date.now()}`;
const AUDIENCE = "team-notes";
const TESTER = { username: "tester", password: `e2e-${randomBytes(12).toString("hex")}` };
const SELECT = 'select[aria-label="When Gen9 asks before using team"]';

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
  const response = await fetch(`${API}${path}`, { method, headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  return { status: response.status, body: response.status === 204 ? null : await response.json().catch(() => null) };
}
// Keycloak's Admin API as the bootstrap admin (a fresh token each time: they last a minute)
async function admin(method, path, body) {
  const token = await fetch(`${KEYCLOAK}/realms/master/protocol/openid-connect/token`, {
    method: "POST",
    body: new URLSearchParams({ grant_type: "password", client_id: "admin-cli", username: env.KC_BOOTSTRAP_ADMIN_USERNAME, password: env.KC_BOOTSTRAP_ADMIN_PASSWORD }),
  })
    .then((r) => r.json())
    .then((b) => b.access_token);
  const response = await fetch(`${KEYCLOAK}/admin/realms${path}`, { method, headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  if (!response.ok && response.status !== 404) throw new Error(`Keycloak ${method} ${path}: ${response.status} ${(await response.text()).slice(0, 200)}`);
  const text = await response.text();
  return text ? JSON.parse(text) : null;
}
const psql = (sql) => execFileSync("docker", ["exec", "gen9-postgres-postgres-1", "psql", "-U", "postgres", "-d", "gen9_agent", "-tAc", sql], { encoding: "utf8" }).trim();
async function buttonWithText(page, scope, text) {
  for (const b of await page.$$(`${scope} button`)) if ((await b.evaluate((e) => e.textContent.trim())) === text) return b;
  throw new Error(`no ${text} button in ${scope}`);
}
// The sessions of the client Gen9 registered, online and offline: Gen9 asks for offline_access
// where the realm offers it (SEP-2207, in the MCP SDK's scope choice), so its sign-in is an
// offline session
async function clientSessions(clientId) {
  const stats = (await admin("GET", `/${REALM}/client-session-stats`)) ?? [];
  const mine = stats.find((c) => c.clientId === clientId);
  return mine ? Number(mine.active) + Number(mine.offline) : 0;
}

const alan = mkdtempSync(join(tmpdir(), "gen9-kc-"));
const chats = [];
let server = null;
try {
  // The throwaway realm
  await admin("POST", "", { realm: REALM, enabled: true, attributes: { frontendUrl: "http://host.docker.internal:15000" } });
  await admin("POST", `/${REALM}/client-scopes`, {
    name: AUDIENCE,
    protocol: "openid-connect",
    attributes: { "include.in.token.scope": "false" },
    protocolMappers: [{ name: "audience", protocol: "openid-connect", protocolMapper: "oidc-audience-mapper", config: { "included.custom.audience": AUDIENCE, "access.token.claim": "true", "id.token.claim": "false" } }],
  });
  const scope = (await admin("GET", `/${REALM}/client-scopes`)).find((s) => s.name === AUDIENCE);
  await admin("PUT", `/${REALM}/default-default-client-scopes/${scope.id}`);
  const policies = await admin("GET", `/${REALM}/components?type=org.keycloak.services.clientregistration.policy.ClientRegistrationPolicy`);
  for (const p of policies.filter((p) => p.subType === "anonymous" && p.providerId === "trusted-hosts")) await admin("DELETE", `/${REALM}/components/${p.id}`);
  await admin("POST", `/${REALM}/users`, {
    username: TESTER.username,
    email: `tester@${REALM}.test`,
    firstName: "Team",
    lastName: "Tester",
    enabled: true,
    emailVerified: true,
    credentials: [{ type: "password", value: TESTER.password, temporary: false }],
  });
  const meta = await fetch(`${KEYCLOAK}/realms/${REALM}/.well-known/openid-configuration`).then((r) => r.json());
  check(meta.issuer === `http://host.docker.internal:15000/realms/${REALM}`, "a throwaway realm whose issuer both sides reach", meta.issuer);

  // Its test server
  server = spawn("uv", ["run", "-q", "--with", "fastmcp==4.0.9", "python", "e2e/fixtures/keycloak_mcp.py", String(PORT), REALM, AUDIENCE], { cwd: ROOT, stdio: "ignore", detached: true });
  for (let i = 0; i < 60 && !(await fetch(`http://127.0.0.1:${PORT}/.well-known/oauth-protected-resource/mcp`).then((r) => r.ok, () => false)); i++) await sleep(500);

  check(Boolean(await signInTerminal({ email: env.GEN9_SEED_USER_EMAIL, password: env.GEN9_SEED_USER_PASSWORD, configDir: alan })), "the seeded user signs in on the terminal");
  for (const c of (await api(alan, "GET", "/v1/me/connectors")).body ?? []) if (c.name === "team") await api(alan, "DELETE", `/v1/me/connectors/${c.id}`);
  const allowed = readFileSync(`${ROOT}gen9-agent/.env`, "utf8");
  if (!check(allowed.includes(`host.docker.internal:${PORT}`) && allowed.includes("host.docker.internal:15000"), "gen9-agent allows the test server and the realm")) {
    throw new Error(`add host.docker.internal:${PORT} and host.docker.internal:15000 to CONNECTORS_ALLOWED_HOSTS in gen9-agent/.env (make setup does), then make up STACKS=agent`);
  }

  const browser = await launch({
    headless: !process.env.HEADED,
    defaultViewport: { width: 1280, height: 900 },
    args: ["--host-resolver-rules=MAP host.docker.internal 127.0.0.1"],
  });
  try {
    const page = await browser.newPage();
    await page.goto(`${APP}/auth/login?returnTo=/settings`, { waitUntil: "networkidle0" });
    await page.type("#username", env.GEN9_SEED_USER_EMAIL);
    await page.type("#password", env.GEN9_SEED_USER_PASSWORD);
    await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }), page.click("#kc-login")]);
    if (!page.url().includes("/settings")) await page.goto(`${APP}/settings`, { waitUntil: "networkidle0" });

    // 1. Add it: registered by DCR, signed in at the realm, and back
    await (await buttonWithText(page, "main", "Add a connector")).click();
    const form = 'form[aria-label="Add a connector"]';
    await page.waitForSelector(form);
    await page.type(`${form} input:not([type=url]):not([type=password])`, "team");
    await page.type(`${form} input[type=url]`, SERVER);
    const went = [];
    page.on("framenavigated", (f) => f === page.mainFrame() && went.push(f.url().slice(0, 100)));
    await (await buttonWithText(page, form, "Add")).click();
    const atRealm = await page.waitForFunction((realm) => location.href.startsWith(`http://host.docker.internal:15000/realms/${realm}/`) && document.querySelector("#username"), { timeout: 60_000 }, REALM).then(() => true, () => false);
    const said = atRealm ? "" : await page.$eval('[role="status"], [role="alert"], form[aria-label="Add a connector"] p', (n) => n.textContent).catch(() => "");
    if (!check(atRealm, "adding it sends the browser to the realm's sign-in", atRealm ? page.url().slice(0, 80) : `went ${went.join(" -> ") || "nowhere"}; says: ${said}`)) throw new Error("no sign-in at the realm");
    await page.type("#username", TESTER.username);
    await page.type("#password", TESTER.password);
    await Promise.all([page.waitForNavigation({ waitUntil: "load", timeout: 60_000 }), page.click("#kc-login")]);
    // A client registered anonymously must ask for consent (the realm's Consent Required policy)
    const consent = await page.$('input[name="accept"], button[name="accept"]');
    if (consent) await Promise.all([page.waitForNavigation({ waitUntil: "load", timeout: 60_000 }), consent.click()]);
    await page.waitForFunction(() => location.pathname === "/settings" && new URLSearchParams(location.search).has("sign_in"), { timeout: 60_000 });
    const notice = await page.$eval('[role="status"], [role="alert"]', (n) => n.textContent).catch(() => "");
    const row = await page.evaluate((select) => document.querySelector(select)?.closest("div.grid")?.textContent ?? "", SELECT);
    const how = psql("select sign_in->>'how' || ' ' || (sign_in->>'client_id') from connectors where name = 'team'");
    check(notice.includes("Signed in") && /1 tool/.test(row) && how.startsWith("dcr "), "Gen9 registered by DCR, the tester signed in (and consented), and it's signed in with its tool", `${notice}; ${how}; consent asked: ${Boolean(consent)}`);
    const clientId = how.split(" ")[1];

    // 2. A chat uses it
    await page.select(SELECT, "never");
    for (let i = 0; i < 40 && psql("select policy from connectors where name = 'team'") !== "never"; i++) await sleep(250);
    await page.goto(`${APP}/chat`, { waitUntil: "networkidle0" });
    await page.type("#composer", "Use your team connector's team_note tool and tell me exactly what the note says.");
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => /\/chat\/[0-9a-f-]{36}/.test(location.pathname), { timeout: 30_000 }).catch(async (e) => {
      const state = await page.evaluate(() => ({ path: location.pathname, composer: document.querySelector("#composer")?.value ?? null, stop: Boolean(document.querySelector('button[aria-label="Stop"]')), items: document.querySelectorAll("ol[aria-live] > li").length })).catch(() => ({}));
      throw new Error(`the new chat's address: ${e.message} (${JSON.stringify(state)})`);
    });
    chats.push(page.url().match(/\/chat\/([0-9a-f-]{36})/)?.[1]);
    await page.waitForSelector('button[aria-label="Stop"]', { timeout: 30_000 }).catch(() => {});
    await page.waitForFunction(() => !document.querySelector('button[aria-label="Stop"]'), { timeout: 240_000, polling: 500 });
    const answer = await page.$$eval("ol[aria-live] > li", (lis) => lis.at(-1)?.textContent ?? "");
    check(/harbour lights are checked every Tuesday/i.test(answer), "a chat uses its tool with Keycloak's token", answer.slice(0, 90));

    // 3. Remove: revoked at Keycloak
    const before = await clientSessions(clientId);
    await page.goto(`${APP}/settings`, { waitUntil: "networkidle0" });
    const select = await page.$(SELECT);
    const remove = await select.evaluateHandle((s) => [...s.parentElement.querySelectorAll("button")].find((b) => b.textContent.trim() === "Remove"));
    await remove.click();
    await page.waitForSelector('[role="alertdialog"]');
    await (await buttonWithText(page, '[role="alertdialog"]', "Remove")).click();
    await page.waitForFunction((s) => !document.querySelector(s), { timeout: 30_000 }, SELECT);
    const after = await clientSessions(clientId);
    check(before >= 1 && after === 0, "Remove revokes its tokens at Keycloak, ending the client's (offline) session", `${before} session(s) before, ${after} after`);

    await page.evaluate(() => document.querySelector('form[action="/auth/logout"]')?.requestSubmit()).catch(() => {});
    await sleep(1500);
  } finally {
    await browser.close();
  }
} catch (e) {
  check(false, "the Keycloak check ran to the end", e.message);
} finally {
  for (const c of (await api(alan, "GET", "/v1/me/connectors").catch(() => ({ body: [] }))).body ?? []) {
    if (c.name === "team") await api(alan, "DELETE", `/v1/me/connectors/${c.id}`).catch(() => {});
  }
  for (const chat of chats.filter(Boolean)) await api(alan, "DELETE", `/v1/threads/${chat}`).catch(() => {});
  await gen9(alan, ["logout"]).catch(() => {});
  rmSync(alan, { recursive: true, force: true });
  await admin("DELETE", `/${REALM}`).catch(() => {});
  if (server) process.kill(-server.pid);
}

console.log(failures ? `\n${failures} check(s) failed` : "\nall checks passed");
process.exit(failures ? 1 : 0);
