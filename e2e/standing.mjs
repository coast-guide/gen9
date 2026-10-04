// Work done for a person ends with their access (gen9-agent's standing.py; docs/plans/harness.md,
// "Auth across the harness"). A throwaway person makes a task with an API trigger; then:
//   1. enabled, the trigger fires and its run answers (the control)
//   2. disabled in Keycloak (as an admin would, in its console), a minute later (each process
//      keeps Keycloak's answer that long): the trigger is refused with 403 and makes no chat; the
//      task's Schedule firing on its own makes none either
//   3. a message queued while they were enabled (the worker stopped meanwhile) doesn't run once
//      they're disabled: it ends as an error, with no answer
//   4. enabled again, after a minute (each process keeps Keycloak's answer that long), the
//      trigger fires and answers again
// The worker is stopped for a moment in step 3, so nothing else should use it meanwhile. It
// deletes the person and their task, and costs two short replies.
import { execFileSync, spawn, spawnSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { ROOT, signInTerminal } from "./signin.mjs";
import { forget } from "./forget.mjs";

const env = Object.fromEntries(
  readFileSync(`${ROOT}gen9-keycloak/.env`, "utf8")
    .split("\n")
    .filter((line) => /^[A-Z0-9_]+=/.test(line))
    .map((line) => [line.slice(0, line.indexOf("=")), line.slice(line.indexOf("=") + 1)]),
);
const API = process.env.GEN9_API ?? "http://localhost:17000";
const KEYCLOAK = process.env.KEYCLOAK_URL ?? "http://localhost:15000";
const WORKER = "gen9-agent-worker-1";
const EMAIL = `standing-${Date.now()}@gen9.test`;
const PASSWORD = `e2e-${randomBytes(12).toString("hex")}`;
const PHRASE = `still-here-${randomBytes(3).toString("hex")}`;

let failures = 0;
function check(ok, what, detail = "") {
  console.log(`${ok ? "ok  " : "FAIL"}  ${what}${detail ? `: ${detail}` : ""}`);
  if (!ok) failures++;
  return ok;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const psql = (query) => execFileSync("docker", ["exec", "gen9-postgres-postgres-1", "psql", "-U", "postgres", "-d", "gen9_agent", "-tAc", query], { encoding: "utf8" }).trim();
const docker = (...args) => spawnSync("docker", args, { encoding: "utf8" });
async function healthy(name) {
  for (let i = 0; i < 90; i++) {
    if (docker("inspect", "-f", "{{.State.Health.Status}}", name).stdout.trim() === "healthy") return true;
    await sleep(2000);
  }
  return false;
}
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
const fire = async (taskId, token) => {
  const response = await fetch(`${API}/v1/tasks/${taskId}/fire`, { method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: "{}" });
  return { status: response.status, text: await response.text() };
};
const chatsOf = (taskId) => Number(psql(`select count(*) from threads where task_id = '${taskId}'`));
const answered = (taskId) => Number(psql(`select count(*) from runs r join threads t on t.id = r.thread_id where t.task_id = '${taskId}' and r.status = 'success'`));
async function until(test, seconds) {
  for (let i = 0; i < seconds; i++) {
    if (await test()) return true;
    await sleep(1000);
  }
  return false;
}
// The task's Schedule fires now, as it would at its time (from inside the worker, which holds
// Temporal's connection)
const triggerSchedule = (taskId) =>
  execFileSync("docker", ["exec", WORKER, "python", "-c", `
import asyncio, httpx
from gen9_agent.settings import Settings
from gen9_agent.temporal import connect
from gen9_agent.keycloak_admin import KeycloakAdmin
async def main():
    s = Settings()
    async with httpx.AsyncClient() as http:
        kc = KeycloakAdmin(s, http) if s.keycloak_admin_client_secret else None
        client = await connect(s, "e2e-standing", kc)
        await client.get_schedule_handle("task-${taskId}").trigger()
        print("triggered")
asyncio.run(main())`], { encoding: "utf8" }).trim();

const dir = mkdtempSync(join(tmpdir(), "gen9-standing-"));
let userId;
let taskId;
try {
  await admin("/users", {
    method: "POST",
    body: JSON.stringify({ username: EMAIL, email: EMAIL, firstName: "Standing", lastName: "Check", enabled: true, emailVerified: true, credentials: [{ type: "password", value: PASSWORD, temporary: false }] }),
  });
  userId = (await admin(`/users?email=${encodeURIComponent(EMAIL)}&exact=true`))[0].id;
  check(Boolean(await signInTerminal({ email: EMAIL, password: PASSWORD, configDir: dir })), "a throwaway person signs in on the terminal");
  taskId = (await api(dir, "POST", "/v1/tasks", { name: "e2e standing", prompt: `Reply with only: ${PHRASE}`, schedule: { kind: "daily", time: "03:00" }, time_zone: "UTC" })).body.id;
  const trigger = (await api(dir, "POST", `/v1/tasks/${taskId}/trigger`)).body.token;

  // 1. Enabled
  const first = await fire(taskId, trigger);
  check(first.status === 202 && (await until(() => answered(taskId) === 1, 120)), "enabled, the trigger fires and its run answers", `${first.status}; ${answered(taskId)} answered`);

  // 2. Disabled: after a minute (what step 1 left in each process's memory lasts that long), the
  // trigger and the Schedule do nothing
  await enable(userId, false);
  await sleep(62_000);
  const refused = await fire(taskId, trigger);
  check(refused.status === 403 && /can't use Gen9 right now/.test(refused.text) && chatsOf(taskId) === 1, "disabled in Keycloak, a minute later the trigger is refused with 403 and makes no chat", `${refused.status} ${refused.text}; ${chatsOf(taskId)} chat(s)`);
  triggerSchedule(taskId);
  await sleep(10_000);
  check(chatsOf(taskId) === 1, "the task's Schedule firing on its own makes no chat either", `${chatsOf(taskId)} chat(s)`);

  // 3. A message queued while enabled doesn't run once they're disabled
  await enable(userId, true);
  docker("stop", WORKER);
  const chat = (await api(dir, "POST", "/v1/threads")).body.id;
  const queued = await api(dir, "POST", `/v1/threads/${chat}/runs`, { message: `Reply with only: ${PHRASE}`, permission_mode: "auto" });
  await enable(userId, false);
  docker("start", WORKER);
  check(await healthy(WORKER), "the worker is back");
  const ended = await until(() => psql(`select status from runs where id = '${queued.body?.id}'`) === "error", 90);
  const error = psql(`select coalesce(error, '') from runs where id = '${queued.body?.id}'`);
  const said = Number(psql(`select count(*) from run_events where run_id = '${queued.body?.id}' and type = 'message.completed'`));
  check(queued.status === 202 && ended && /PersonInactive/.test(error) && said === 0, "a message queued before they were disabled ends as an error, with no answer", `${queued.status}; ${psql(`select status from runs where id = '${queued.body?.id}'`)}; ${error.slice(0, 60)}; ${said} answer(s)`);

  // 4. Enabled again: a minute later, the trigger answers again
  await enable(userId, true);
  await sleep(62_000);
  const again = await fire(taskId, trigger);
  check(again.status === 202 && (await until(() => answered(taskId) === 2, 120)), "enabled again, a minute later the trigger fires and answers again", `${again.status}; ${answered(taskId)} answered`);
} catch (e) {
  check(false, "the standing check ran to the end", e.stack ?? String(e));
} finally {
  if (docker("inspect", "-f", "{{.State.Running}}", WORKER).stdout.trim() !== "true") docker("start", WORKER);
  if (userId) {
    await enable(userId, true).catch(() => {});
    if (taskId) await api(dir, "DELETE", `/v1/tasks/${taskId}`).catch(() => {});
    await admin(`/users/${userId}`, { method: "DELETE" }).catch(() => {});
    forget(userId);
  }
  rmSync(dir, { recursive: true, force: true });
}
console.log(failures ? `\n${failures} check(s) failed` : "\nall standing checks passed");
process.exit(failures ? 1 : 0);
