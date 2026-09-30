// Who did what (gen9-agent's audit.py; docs/plans/harness.md, "Auth across the harness"; OWASP
// ASVS 5.0 V16):
//   1. the seeded admin makes a throwaway person an admin and takes it back, in Chrome on Users:
//      Gen9 records both with the admin as the actor, while Keycloak's own admin events name
//      gen9-agent's service account (why Gen9 keeps its own record)
//   2. the seeded user adds and removes an environment secret, a connector and a task's trigger:
//      each recorded with them as the actor, and no secret's value anywhere in the record
//   3. the throwaway person tries the seeded user's chat (404) and an admin route (403): both
//      recorded as denied; a made-up id isn't recorded
//   4. the record can't be changed, deleted or truncated
//   5. the admin reads it in Chrome on Audit log, in plain words: the role changes, and under
//      "Refused access" the throwaway person's tries; axe clean on both
// It deletes what it made, and costs no model call.
import { execFileSync } from "node:child_process";
import { randomBytes, randomUUID } from "node:crypto";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawn } from "node:child_process";
import { launch } from "./browser.mjs";
import { deleteChats } from "./chats.mjs";
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
const TAG = randomBytes(3).toString("hex");
const EMAIL = `audit-${Date.now()}@gen9.test`;
const PASSWORD = `e2e-${randomBytes(12).toString("hex")}`;
const AXE = readFileSync(createRequire(import.meta.url).resolve("axe-core/axe.min.js"), "utf8");
const SECRET_VALUE = `never-logged-${randomBytes(8).toString("hex")}`;

let failures = 0;
function check(ok, what, detail = "") {
  console.log(`${ok ? "ok  " : "FAIL"}  ${what}${detail ? `: ${detail}` : ""}`);
  if (!ok) failures++;
  return ok;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const psql = (query) => execFileSync("docker", ["exec", "gen9-postgres-postgres-1", "psql", "-U", "postgres", "-d", "gen9_agent", "-tAc", query], { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }).trim();
const refused = (query) => {
  try {
    psql(query);
    return "";
  } catch (e) {
    return String(e.stderr ?? e.message);
  }
};
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
const whoami = (dir) => new Promise((resolve) => spawn("uv", ["run", "-q", "gen9", "whoami"], { cwd: `${ROOT}gen9-cli`, env: { ...process.env, GEN9_CONFIG_DIR: dir }, stdio: "ignore" }).on("exit", resolve));
async function api(dir, method, path, body) {
  await whoami(dir);
  const token = JSON.parse(readFileSync(join(dir, "credentials.json"), "utf8")).access_token;
  const response = await fetch(`${API}${path}`, { method, headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  return { status: response.status, body: await response.json().catch(() => null) };
}
// Run inside gen9-agent's containers with their own DATABASE_* settings: prints who it is, then
// "refused …" or "ALLOWED …" per statement, never a value
const SERVICES_ROLE_TRIES = 11;
const SERVICES_ROLE_PROBE = `
import asyncio, os
import psycopg

TRIES = [
    "update audit_events set action = action",
    "delete from audit_events where false",
    "truncate audit_events",
    "alter table audit_events disable trigger audit_events_append_only",
    "drop trigger audit_events_append_only on audit_events",
    "create or replace function audit_events_append_only() returns trigger language plpgsql as 'begin return new; end'",
    "drop table audit_events",
    "create table e2e_evil (id int)",
    "create temp table e2e_evil (id int)",
    "set role gen9_agent",
    "set session_replication_role = replica",
]

async def main():
    conninfo = psycopg.conninfo.make_conninfo(
        host=os.environ["DATABASE_HOST"], port=os.environ["DATABASE_PORT"], dbname=os.environ["DATABASE_NAME"],
        user=os.environ["DATABASE_USER"], password=os.environ["DATABASE_PASSWORD"])
    async with await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as conn:
        print("as", (await (await conn.execute("select current_user")).fetchone())[0])
        for sql in TRIES:
            try:
                await conn.execute(sql)
                print("ALLOWED", sql[:40])
            except psycopg.Error as e:
                print("refused", str(e).splitlines()[0])

asyncio.run(main())
`;
const subOf = (email) => psql(`select sub from users where email = '${email}'`);
// The audit rows since this check began, as "actor|action|outcome|target|where|detail"
const since = new Date().toISOString();
const events = (where = "true") => psql(`select actor || '|' || action || '|' || outcome || '|' || coalesce(target, '') || '|' || coalesce("where", '') || '|' || detail::text from audit_events where at >= '${since}' and ${where} order by id`).split("\n").filter(Boolean);

const alan = mkdtempSync(join(tmpdir(), "gen9-audit-alan-"));
const other = mkdtempSync(join(tmpdir(), "gen9-audit-other-"));
const browser = await launch({ headless: !process.env.HEADED, defaultViewport: { width: 1280, height: 900 } });
const page = await browser.newPage();
let otherId;
let chat;
try {
  await admin("/users", {
    method: "POST",
    body: JSON.stringify({ username: EMAIL, email: EMAIL, firstName: "Audit", lastName: "Check", enabled: true, emailVerified: true, credentials: [{ type: "password", value: PASSWORD, temporary: false }] }),
  });
  otherId = (await admin(`/users?email=${encodeURIComponent(EMAIL)}&exact=true`))[0].id;
  check(Boolean(await signInTerminal({ email: env.GEN9_SEED_USER_EMAIL, password: env.GEN9_SEED_USER_PASSWORD, configDir: alan })), "the seeded user signs in on the terminal");
  check(Boolean(await signInTerminal({ email: EMAIL, password: PASSWORD, configDir: other })), "a throwaway person signs in on the terminal");

  // 1. An admin's action, in Chrome
  await page.goto(`${APP}/auth/login?returnTo=${encodeURIComponent(`/admin/users?q=${EMAIL}`)}`, { waitUntil: "networkidle0" });
  await page.type("#username", env.GEN9_SEED_ADMIN_EMAIL);
  await page.type("#password", env.GEN9_SEED_ADMIN_PASSWORD);
  await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }), page.click("#kc-login")]);
  if (!page.url().includes("/admin/users")) await page.goto(`${APP}/admin/users?q=${encodeURIComponent(EMAIL)}`, { waitUntil: "networkidle0" });
  const menu = async (item) => {
    await page.waitForSelector('button[aria-label^="Actions for"]');
    await page.click('button[aria-label^="Actions for"]');
    await page.waitForSelector('[role="menuitem"]');
    const items = await page.$$('[role="menuitem"]');
    for (const it of items) if ((await it.evaluate((e) => e.textContent.trim())) === `${item}…`) return confirm(it, item);
    throw new Error(`no menu item ${item}`);
  };
  // A change to someone's access asks first: the dialog's button says it again
  const confirm = async (it, item) => {
    await it.click();
    await page.waitForSelector('[role="alertdialog"]');
    for (const button of await page.$$('[role="alertdialog"] button'))
      if ((await button.evaluate((e) => e.textContent.trim())) === item) return button.click();
    throw new Error(`no ${item} in the dialog`);
  };
  await menu("Make admin");
  const promoted = async () => (await admin(`/users/${otherId}/groups`)).some((g) => g.name === "admins");
  for (let i = 0; i < 20 && !(await promoted()); i++) await sleep(500);
  await page.reload({ waitUntil: "networkidle0" });
  await menu("Remove admin access");
  for (let i = 0; i < 20 && (await promoted()); i++) await sleep(500);
  const ada = subOf(env.GEN9_SEED_ADMIN_EMAIL);
  const updates = events(`action = 'admin.user.update' and target = '${otherId}'`);
  check(
    updates.length === 2 && updates.every((e) => e.startsWith(`${ada}|admin.user.update|success|${otherId}|PATCH /v1/admin/users/{user_id}|`)) && updates[0].endsWith('{"admin": true}') && updates[1].endsWith('{"admin": false}'),
    "the admin's two role changes are recorded with the admin as the actor",
    updates.map((e) => e.split("|").slice(1, 4).concat(e.split("|").at(-1)).join(" ")).join("; "),
  );
  // The role changes are group memberships (the user's creation, by this check, is left out)
  const kcEvents = ((await admin(`/admin-events?resourcePath=${encodeURIComponent(`users/${otherId}*`)}&max=50`)) ?? []).filter((e) => e.resourcePath?.includes("/groups/"));
  const actors = [...new Set(kcEvents.map((e) => e.authDetails?.userId))];
  const actor = actors.length === 1 ? await admin(`/users/${actors[0]}`) : null;
  check(kcEvents.length === 2 && actors[0] !== ada && /^service-account-/.test(actor?.username ?? ""), "Keycloak's own admin events for the same changes name gen9-agent's service account, not the admin", `${kcEvents.length} event(s) by ${actor?.username ?? actors.join(", ")}`);

  // 2. The seeded user's security actions
  const secret = (await api(alan, "POST", "/v1/me/environment-secrets", { name: `audit-${TAG}`, host: "httpbin.org", value: SECRET_VALUE })).body;
  await api(alan, "DELETE", `/v1/me/environment-secrets/${secret.id}`);
  const connector = (await api(alan, "POST", "/v1/me/connectors", { name: `audit-${TAG}`, url: "https://mcp.deepwiki.com/mcp" })).body;
  await api(alan, "DELETE", `/v1/me/connectors/${connector.id}`);
  const task = (await api(alan, "POST", "/v1/tasks", { name: `e2e audit ${TAG}`, prompt: "Reply with only: ok", schedule: { kind: "daily", time: "03:00" }, time_zone: "UTC" })).body;
  try {
    await api(alan, "POST", `/v1/tasks/${task.id}/trigger`);
    await api(alan, "DELETE", `/v1/tasks/${task.id}/trigger`);
  } finally {
    // A task left behind would fire every day (and spend) until someone deletes it
    await api(alan, "DELETE", `/v1/tasks/${task.id}`).catch(() => {});
  }
  const theirs = events(`actor = '${subOf(env.GEN9_SEED_USER_EMAIL)}' and outcome = 'success'`).map((e) => `${e.split("|")[1]} ${e.split("|")[3]}`);
  const expected = [`environment_secret.add ${secret.id}`, `environment_secret.remove ${secret.id}`, `connector.add ${connector.id}`, `connector.remove ${connector.id}`, `task.trigger.make ${task.id}`, `task.trigger.revoke ${task.id}`];
  check(expected.every((e) => theirs.includes(e)), "the seeded user's secret, connector and trigger, added and removed, are recorded as theirs", theirs.join("; "));
  check(events().every((e) => !e.includes(SECRET_VALUE)) && events(`action like 'environment_secret.%'`).every((e) => e.includes("httpbin.org")), "no secret's value is in the record, only its name and host");

  // 3. Refused
  chat = (await api(alan, "POST", "/v1/threads")).body.id;
  const tried = await api(other, "GET", `/v1/threads/${chat}`);
  const madeUp = await api(other, "GET", `/v1/threads/${randomUUID()}`);
  const adminOnly = await api(other, "POST", "/v1/admin/search/reindex");
  const b = subOf(EMAIL);
  const denied = events(`actor = '${b}' and outcome = 'denied'`).map((e) => `${e.split("|")[1]} ${e.split("|")[3]} ${e.split("|")[4]}`);
  check(tried.status === 404 && denied.includes(`thread.access ${chat} GET /v1/threads/{thread_id}`), "another person trying the seeded user's chat gets 404, recorded as denied", `${tried.status}; ${denied.join("; ")}`);
  check(madeUp.status === 404 && denied.length === 2, "a made-up id isn't recorded (it names nobody's chat)", `${denied.length} denied`);
  check(adminOnly.status === 403 && denied.includes("access.refused  POST /v1/admin/search/reindex"), "an admin route refusing them with 403 is recorded as denied", `${adminOnly.status}`);

  // 4. Append-only
  const tries = [
    refused(`update audit_events set actor = 'someone' where at >= '${since}'`),
    refused(`delete from audit_events where at >= '${since}'`),
    refused("truncate audit_events"),
  ];
  check(tries.every((t) => /append-only/.test(t)), "the record can't be changed, deleted or truncated", tries.map((t) => (/append-only/.test(t) ? "refused" : `allowed ${t.slice(0, 40)}`)).join(", "));
  // The API and the worker connect as a role that owns nothing (gen9_agent_app): with their own
  // credentials, from inside each container, they can't change the record or undo its protection
  for (const service of ["api", "worker"]) {
    const outcome = execFileSync("docker", ["exec", "-i", `gen9-agent-${service}-1`, "python", "-"], { input: SERVICES_ROLE_PROBE, encoding: "utf8" }).trim().split("\n");
    const [who, ...rest] = outcome;
    check(
      who === "as gen9_agent_app" && rest.length === SERVICES_ROLE_TRIES && rest.every((line) => line.startsWith("refused")),
      `the ${service}'s own database role can't change the record, disable its trigger, replace its function, drop it or create anything`,
      `${who}; ${rest.filter((line) => !line.startsWith("refused")).join(", ") || `${rest.length} refused`}`,
    );
  }

  // 5. The admin reads it
  const rows = async () => page.$$eval('ol[aria-label="Events, newest first"] > li', (lis) => lis.map((li) => li.innerText.replace(/\s+/g, " ")));
  const axe = async () => {
    await page.evaluate(AXE);
    const { violations } = await page.evaluate(() => window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] } }));
    return violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => v.id);
  };
  await page.goto(`${APP}/admin/audit`, { waitUntil: "networkidle0" });
  const everything = await rows();
  const admin_ = env.GEN9_SEED_ADMIN_EMAIL;
  check(
    everything.some((r) => r.includes(admin_) && r.includes(`Made ${EMAIL} an admin`)) && everything.some((r) => r.includes(admin_) && r.includes(`Removed admin access from ${EMAIL}`)),
    "Audit log shows the admin making the person an admin and taking it back, in plain words",
    everything.slice(0, 3).join(" | "),
  );
  // No row shows an action code (six once did: gen9-learn.md, M9, F9; lib/audit-words.test.ts guards each)
  const coded = everything.filter((r) => /\b(?:account|admin|connector|environment_secret|task|thread|run|file|operator|restore|access)\.[a-z_]+(?:\.[a-z_]+)*\b/.test(r));
  check(coded.length === 0, "every row says what happened in words, never an action code", `${everything.length} rows${coded.length ? `; coded: ${coded.slice(0, 2).join(" | ")}` : ""}`);
  const secretAdded = everything.find((r) => r.includes(`Added the environment secret audit-${TAG}`)) ?? "";
  check(secretAdded.includes(`audit-${TAG} for httpbin.org, for reading only`), "Audit log says what a secret was added for", secretAdded.slice(0, 160));
  const blocking = await axe();
  check(!blocking.length, "Audit log has no serious accessibility violations", blocking.join(", "));
  await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }), page.click('nav[aria-label="Show"] a[href$="show=refused"]')]);
  const refusedRows = await rows();
  check(
    refusedRows.every((r) => r.includes("Refused")) && refusedRows.some((r) => r.includes(EMAIL) && r.includes("Tried to open someone else’s chat")) && refusedRows.some((r) => r.includes(EMAIL) && r.includes("Was refused something only admins may do")),
    "under Refused access, the throwaway person's two tries, each marked Refused",
    refusedRows.slice(0, 2).join(" | "),
  );
  const blockingRefused = await axe();
  check(!blockingRefused.length, "Refused access has no serious accessibility violations", blockingRefused.join(", "));
} catch (e) {
  check(false, "the audit check ran to the end", e.stack ?? String(e));
} finally {
  if (chat) await deleteChats(alan, [chat]).catch(() => {});
  if (otherId) await admin(`/users/${otherId}`, { method: "DELETE" }).catch(() => {});
  await browser.close();
  rmSync(alan, { recursive: true, force: true });
  rmSync(other, { recursive: true, force: true });
}
console.log(failures ? `\n${failures} check(s) failed` : "\nall audit checks passed");
process.exit(failures ? 1 : 0);
