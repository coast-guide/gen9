// Stopping agents (manual-e2e.md, P5-C10; OWASP ASI10, rogue agents): nothing of a disabled person's
// acts after, and an operator can stop every agent at once. A throwaway person with a scheduled
// task, then:
//   1. disabled in Keycloak's own console while a long turn runs: the turn ends within about a
//      minute (each process keeps Keycloak's answer that long) as an error, before its steps run
//      out, whatever started it (gen9-agent's standing.StillActive)
//   2. disabled by an admin in Gen9 (Chrome, Admin > Users) with one turn running and one waiting
//      for Allow: both end at once, cancelled, and the audit event says how many
//   3. `make stop-agents` with a turn running: the turn ends, the worker stops, the task's
//      Schedule is paused with the operator's note; `make resume-agents`: the worker is back and
//      the Schedule runs again; both are audit events
// It deletes the person and their task, stops the worker for a moment in step 3 (nothing else
// should use it meanwhile), and costs a few short steps of three turns.
import { execFileSync, spawn, spawnSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { launch } from "./browser.mjs";
import { ROOT, signInTerminal } from "./signin.mjs";
import { secondStep } from "./second-step.mjs";

const env = Object.fromEntries(
  readFileSync(`${ROOT}gen9-keycloak/.env`, "utf8")
    .split("\n")
    .filter((line) => /^[A-Z0-9_]+=/.test(line))
    .map((line) => [line.slice(0, line.indexOf("=")), line.slice(line.indexOf("=") + 1)]),
);
const API = process.env.GEN9_API ?? "http://localhost:17000";
const APP = process.env.APP_URL ?? "http://localhost:14000";
const KEYCLOAK = process.env.KEYCLOAK_URL ?? "http://localhost:15000";
const WORKER = "gen9-agent-worker-1";
const EMAIL = `stop-${Date.now()}@gen9.test`;
const PASSWORD = `e2e-${randomBytes(12).toString("hex")}`;
// Many short steps: a turn that outlasts the minute a disable takes to be seen
const LONG = "In your environment, run the command `sleep 6` fifteen times, one command per step, never two at once, then reply with only: done.";

let failures = 0;
function check(ok, what, detail = "") {
  console.log(`${ok ? "ok  " : "FAIL"}  ${what}${detail ? `: ${detail}` : ""}`);
  if (!ok) failures++;
  return ok;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const psql = (query) => execFileSync("docker", ["exec", "gen9-postgres-postgres-1", "psql", "-U", "postgres", "-d", "gen9_agent", "-tAc", query], { encoding: "utf8" }).trim();
const docker = (...args) => spawnSync("docker", args, { encoding: "utf8" });
const make = (target) => spawnSync("make", [target], { cwd: ROOT, encoding: "utf8" });
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
const enable = (id, enabled) => admin(`/users/${id}`, { method: "PUT", body: JSON.stringify({ enabled }) });
const whoami = (dir) => new Promise((resolve) => spawn("uv", ["run", "-q", "gen9", "whoami"], { cwd: `${ROOT}gen9-cli`, env: { ...process.env, GEN9_CONFIG_DIR: dir }, stdio: "ignore" }).on("exit", resolve));
async function api(dir, method, path, body) {
  await whoami(dir);
  const token = JSON.parse(readFileSync(join(dir, "credentials.json"), "utf8")).access_token;
  const response = await fetch(`${API}${path}`, { method, headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  return { status: response.status, body: await response.json().catch(() => null) };
}
const statusOf = (run) => psql(`select status from runs where id = '${run}'`);
const errorOf = (run) => psql(`select coalesce(error, '') from runs where id = '${run}'`);
const stepsOf = (run) => Number(psql(`select count(*) from run_events where run_id = '${run}' and type = 'tool.started'`));
async function until(test, seconds) {
  for (let i = 0; i < seconds; i++) {
    if (await test()) return true;
    await sleep(1000);
  }
  return false;
}
// A new chat's turn, started; its run's id
async function start(dir, message, mode = "auto") {
  const chat = (await api(dir, "POST", "/v1/threads")).body.id;
  return (await api(dir, "POST", `/v1/threads/${chat}/runs`, { message, permission_mode: mode })).body.id;
}
// What Temporal says of the task's Schedule, from inside the worker (it holds the connection)
const schedule = (taskId) =>
  JSON.parse(
    execFileSync("docker", ["exec", WORKER, "python", "-c", `
import asyncio, json, httpx
from gen9_agent.settings import Settings
from gen9_agent.temporal import connect
from gen9_agent.keycloak_admin import KeycloakAdmin
async def main():
    s = Settings()
    async with httpx.AsyncClient() as http:
        kc = KeycloakAdmin(s, http) if s.keycloak_admin_client_secret else None
        client = await connect(s, "e2e-stop", kc)
        state = (await client.get_schedule_handle("task-${taskId}").describe()).schedule.state
        print(json.dumps({"paused": state.paused, "note": state.note}))
asyncio.run(main())`], { encoding: "utf8" }).trim(),
  );

const dir = mkdtempSync(join(tmpdir(), "gen9-stop-"));
let userId;
let taskId;
try {
  await admin("/users", {
    method: "POST",
    body: JSON.stringify({ username: EMAIL, email: EMAIL, firstName: "Stop", lastName: "Check", enabled: true, emailVerified: true, credentials: [{ type: "password", value: PASSWORD, temporary: false }] }),
  });
  userId = (await admin(`/users?email=${encodeURIComponent(EMAIL)}&exact=true`))[0].id;
  check(Boolean(await signInTerminal({ email: EMAIL, password: PASSWORD, configDir: dir })), "a throwaway person signs in on the terminal");
  taskId = (await api(dir, "POST", "/v1/tasks", { name: "e2e stop", prompt: "Reply with only: ok", schedule: { kind: "daily", time: "03:00" }, time_zone: "UTC" })).body.id;

  // 1. Disabled in Keycloak's console mid-turn
  const first = await start(dir, LONG);
  await until(() => stepsOf(first) >= 1, 120);
  await enable(userId, false);
  const disabledAt = Date.now();
  const ended = await until(() => statusOf(first) === "error", 150);
  const took = Math.round((Date.now() - disabledAt) / 1000);
  check(ended && /PersonInactive/.test(errorOf(first)) && stepsOf(first) < 15 && took <= 90, "disabled in Keycloak's console mid-turn, the turn ends within about a minute, as an error, before its steps run out", `${statusOf(first)} after ${took} s; ${stepsOf(first)} step(s); ${errorOf(first).slice(0, 50)}`);
  await enable(userId, true);
  await sleep(62_000); // each process forgets what it knew of them after a minute

  // 2. Disabled by an admin in Gen9, one turn running and one waiting
  const running = await start(dir, LONG);
  const waiting = await start(dir, "Please remember that my favourite tree is the rowan. Reply with exactly: Noted.", "ask");
  const both = await until(() => stepsOf(running) >= 1 && statusOf(waiting) === "waiting", 180);
  const browser = await launch({ headless: !process.env.HEADED, defaultViewport: { width: 1280, height: 900 } });
  let byAdminAt = 0;
  try {
    const page = await browser.newPage();
    await page.goto(`${APP}/auth/login?returnTo=${encodeURIComponent(`/admin/users?q=${EMAIL}`)}`, { waitUntil: "networkidle0" });
    await page.type("#username", env.GEN9_SEED_ADMIN_EMAIL);
    await page.type("#password", env.GEN9_SEED_ADMIN_PASSWORD);
    await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }), page.click("#kc-login")]);
    await secondStep(page); // admins need a second step: the seeded admin's code
    if (!page.url().includes("/admin/users")) await page.goto(`${APP}/admin/users?q=${encodeURIComponent(EMAIL)}`, { waitUntil: "networkidle0" });
    await page.waitForSelector('button[aria-label^="Actions for"]');
    await page.click('button[aria-label^="Actions for"]');
    await page.waitForSelector('[role="menuitem"]');
    for (const item of await page.$$('[role="menuitem"]')) if ((await item.evaluate((e) => e.textContent.trim())) === "Disable account…") await item.click();
    await page.waitForSelector('[role="alertdialog"]');
    byAdminAt = Date.now();
    for (const button of await page.$$('[role="alertdialog"] button')) if ((await button.evaluate((e) => e.textContent.trim())) === "Disable account") await button.click();
    await page.waitForFunction(() => !document.querySelector('[role="alertdialog"]'), { timeout: 30_000 });
    await page.evaluate(() => document.querySelector('form[action="/auth/logout"]')?.requestSubmit()).catch(() => {});
    await sleep(1500);
  } finally {
    await browser.close();
  }
  const cancelled = await until(() => statusOf(running) === "cancelled" && statusOf(waiting) === "cancelled", 30);
  const stoppedIn = Math.round((Date.now() - byAdminAt) / 1000);
  const recorded = psql(`select coalesce(detail->>'runs_stopped', '') from audit_events where action = 'admin.user.update' and target = '${userId}' order by at desc limit 1`);
  check(both && cancelled && recorded === "2", "disabled by an admin in Gen9, a running turn and one waiting for Allow both end at once, and the audit event counts them", `${statusOf(running)}, ${statusOf(waiting)} within ${stoppedIn} s; audit runs_stopped ${recorded}`);
  await enable(userId, true);
  await sleep(62_000);

  // 3. The operator's stop, then resume
  const third = await start(dir, LONG);
  await until(() => stepsOf(third) >= 1, 120);
  const stopped = make("stop-agents");
  const stoppedRun = statusOf(third);
  const workerDown = docker("inspect", "-f", "{{.State.Running}}", WORKER).stdout.trim() === "false";
  check(stopped.status === 0 && /stopped: \d+ runs, \d+ scheduled tasks paused/.test(stopped.stdout) && stoppedRun === "cancelled" && workerDown, "make stop-agents: the running turn ends, cancelled, and the worker stops", `${stopped.stdout.trim().split("\n").at(-1)}; ${stoppedRun}; worker down ${workerDown}`);
  const resumed = make("resume-agents");
  const after = schedule(taskId);
  const workerUp = docker("inspect", "-f", "{{.State.Health.Status}}", WORKER).stdout.trim() === "healthy";
  check(resumed.status === 0 && /resumed: \d+ scheduled tasks/.test(resumed.stdout) && workerUp && after.paused === false, "make resume-agents: the worker is back, and the task's Schedule runs again", `${resumed.stdout.trim().split("\n").at(-1)}; healthy ${workerUp}; paused ${after.paused}`);
  const audit = psql("select string_agg(action, ',' order by at) from (select action, at from audit_events where action like 'operator.%' order by at desc limit 2) a");
  check(audit === "operator.stop,operator.resume", "both are audit events", audit);
} catch (e) {
  check(false, "the stop check ran to the end", e.stack ?? String(e));
} finally {
  if (docker("inspect", "-f", "{{.State.Running}}", WORKER).stdout.trim() !== "true") make("resume-agents");
  if (userId) {
    await enable(userId, true).catch(() => {});
    if (taskId) await api(dir, "DELETE", `/v1/tasks/${taskId}`).catch(() => {});
    await admin(`/users/${userId}`, { method: "DELETE" }).catch(() => {});
  }
  rmSync(dir, { recursive: true, force: true });
}
console.log(failures ? `\n${failures} check(s) failed` : "\nall stop checks passed");
process.exit(failures ? 1 : 0);
