// Probe: does a disabled person's delegated work still run? A throwaway person makes a task with an
// API trigger, is disabled in Keycloak (as an admin would, in the console or through Gen9), and the
// trigger fires. Prints what happened; deletes the person and the task at the end.
//
//   node gen9-agent/explore/auth/disabled_probe.mjs      (from the repository root, e2e deps installed)
import { execFileSync, spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";

const ROOT = new URL("../../../", import.meta.url).pathname;
const { signInTerminal } = await import(`${ROOT}e2e/signin.mjs`);
const env = Object.fromEntries(
  readFileSync(`${ROOT}gen9-keycloak/.env`, "utf8").split("\n").filter((l) => /^[A-Z0-9_]+=/.test(l)).map((l) => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]),
);
const API = "http://localhost:17000";
const KEYCLOAK = "http://localhost:15000";
const EMAIL = `disabled-probe-${Date.now()}@gen9.test`;
const PASSWORD = `p-${randomBytes(12).toString("hex")}`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const psql = (q) => execFileSync("docker", ["exec", "gen9-postgres-postgres-1", "psql", "-U", "postgres", "-d", "gen9_agent", "-tAc", q], { encoding: "utf8" }).trim();

async function admin(path, init = {}) {
  const token = await fetch(`${KEYCLOAK}/realms/master/protocol/openid-connect/token`, {
    method: "POST",
    body: new URLSearchParams({ grant_type: "password", client_id: "admin-cli", username: env.KC_BOOTSTRAP_ADMIN_USERNAME, password: env.KC_BOOTSTRAP_ADMIN_PASSWORD }),
  }).then((r) => r.json()).then((b) => b.access_token);
  const r = await fetch(`${KEYCLOAK}/admin/realms/gen9${path}`, { ...init, headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" } });
  return [201, 204, 404].includes(r.status) ? null : r.json();
}
const whoami = (dir) => new Promise((res) => spawn("uv", ["run", "-q", "gen9", "whoami"], { cwd: `${ROOT}gen9-cli`, env: { ...process.env, GEN9_CONFIG_DIR: dir }, stdio: "ignore" }).on("exit", res));
async function api(dir, method, path, body) {
  await whoami(dir);
  const token = JSON.parse(readFileSync(join(dir, "credentials.json"), "utf8")).access_token;
  const r = await fetch(`${API}${path}`, { method, headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  return { status: r.status, body: await r.json().catch(() => null) };
}

const dir = mkdtempSync(join(tmpdir(), "gen9-disabled-probe-"));
let userId, taskId;
try {
  await admin("/users", { method: "POST", body: JSON.stringify({ username: EMAIL, email: EMAIL, firstName: "Disabled", lastName: "Probe", enabled: true, emailVerified: true, credentials: [{ type: "password", value: PASSWORD, temporary: false }] }) });
  userId = (await admin(`/users?email=${encodeURIComponent(EMAIL)}&exact=true`))[0].id;
  console.log("signed in:", await signInTerminal({ email: EMAIL, password: PASSWORD, configDir: dir }));
  taskId = (await api(dir, "POST", "/v1/tasks", { name: "probe", prompt: "Reply with only: still running", schedule: { kind: "daily", time: "03:00" }, time_zone: "UTC" })).body.id;
  const trigger = (await api(dir, "POST", `/v1/tasks/${taskId}/trigger`)).body.token;
  await admin(`/users/${userId}`, { method: "PUT", body: JSON.stringify({ enabled: false }) });
  console.log("disabled in Keycloak:", (await admin(`/users/${userId}`)).enabled === false);
  const fired = await fetch(`${API}/v1/tasks/${taskId}/fire`, { method: "POST", headers: { Authorization: `Bearer ${trigger}`, "Content-Type": "application/json" }, body: "{}" });
  console.log("fire:", fired.status, await fired.text());
  let status = "";
  for (let i = 0; i < 90; i++) {
    status = psql(`select coalesce(string_agg(r.status || ':' || coalesce(r.error, ''), ', '), 'no run') from runs r join threads t on t.id = r.thread_id where t.task_id = '${taskId}'`);
    if (/success|error|cancelled|expired/.test(status)) break;
    await sleep(1000);
  }
  console.log("the task's runs:", status);
} finally {
  // Enabled again, so the person can delete their task (with its Schedule) through the API
  if (userId) await admin(`/users/${userId}`, { method: "PUT", body: JSON.stringify({ enabled: true }) });
  if (taskId) console.log("task deleted:", (await api(dir, "DELETE", `/v1/tasks/${taskId}`)).status);
  if (userId) await admin(`/users/${userId}`, { method: "DELETE" });
  rmSync(dir, { recursive: true, force: true });
}
