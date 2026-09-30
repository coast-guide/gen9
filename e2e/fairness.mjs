// Priority and fairness on the agent queue (docs/temporal.md, rule 11; acceptance
// temporal.fairness-priority), through the real path. Scheduled tasks' runs are background runs
// (priority 3, the person as fairness key); a chat run is priority 1. The worker is swapped for one
// with a single agent slot (WORKER_CONCURRENCY=1, `docker compose run`), and restored at the end:
//   1. the seeded user queues three background runs (Run now), then the seeded admin sends a
//      chat message: the chat run starts before the user's queued background runs
//   2. the seeded user queues four background runs, then the admin one: with the person as
//      fairness key, the admin's doesn't wait for all of the user's
// It deletes its tasks and chats at the end, and costs a dozen short replies. Needs every stack up;
// nothing else should use the worker meanwhile.
import { execFileSync, spawn, spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { ROOT, signInTerminal } from "./signin.mjs";

const env = Object.fromEntries(
  readFileSync(`${ROOT}gen9-keycloak/.env`, "utf8")
    .split("\n")
    .filter((line) => /^[A-Z0-9_]+=/.test(line))
    .map((line) => [line.slice(0, line.indexOf("=")), line.slice(line.indexOf("=") + 1)]),
);
const API = process.env.GEN9_API ?? "http://localhost:17000";
const WORKER = "gen9-agent-worker-1";
const ONE_SLOT = "gen9-agent-fairness";

let failures = 0;
function check(ok, what, detail = "") {
  console.log(`${ok ? "ok  " : "FAIL"}  ${what}${detail ? `: ${detail}` : ""}`);
  if (!ok) failures++;
  return ok;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const psql = (sql) => execFileSync("docker", ["exec", "gen9-postgres-postgres-1", "psql", "-U", "postgres", "-d", "gen9_agent", "-tAc", sql], { encoding: "utf8" }).trim();
const gen9 = (configDir, args) =>
  new Promise((resolve) => {
    const child = spawn("uv", ["run", "-q", "gen9", ...args], { cwd: `${ROOT}gen9-cli`, env: { ...process.env, GEN9_CONFIG_DIR: configDir } });
    child.on("exit", (code) => resolve(code));
    child.stdin.end();
  });
async function api(configDir, method, path, body) {
  await gen9(configDir, ["whoami"]);
  const token = JSON.parse(readFileSync(join(configDir, "credentials.json"), "utf8")).access_token;
  const response = await fetch(`${API}${path}`, { method, headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const text = await response.text();
  return { status: response.status, body: text ? JSON.parse(text) : null };
}
const docker = (...args) => spawnSync("docker", args, { encoding: "utf8" });
async function healthy(name) {
  for (let i = 0; i < 90; i++) {
    if (docker("inspect", "-f", "{{.State.Health.Status}}", name).stdout.trim() === "healthy") return true;
    await sleep(2000);
  }
  return false;
}
// A task's chats' runs, oldest first: [thread, status, started_at, queued at (created_at)]
const runsOf = (taskId) =>
  psql(`select t.id, r.status, coalesce(to_char(r.started_at, 'HH24:MI:SS.MS'), ''), to_char(r.created_at, 'HH24:MI:SS.MS') from threads t join runs r on r.thread_id = t.id where t.task_id = '${taskId}' order by r.created_at`)
    .split("\n")
    .filter(Boolean)
    .map((line) => line.split("|"));
const startedAt = (threadId) => psql(`select coalesce(to_char(started_at, 'HH24:MI:SS.MS'), '') from runs where thread_id = '${threadId}'`);
async function allDone(taskIds, extra = []) {
  for (let i = 0; i < 300; i++) {
    const statuses = [...taskIds.flatMap((id) => runsOf(id).map((r) => r[1])), ...extra.map((t) => psql(`select status from runs where thread_id = '${t}'`))];
    if (statuses.length && statuses.every((s) => ["success", "error", "cancelled", "expired"].includes(s))) return statuses;
    await sleep(1000);
  }
  throw new Error("the runs didn't finish in 5 minutes");
}
const task = (name, prompt = "Reply with the single word: ready") => ({ name, prompt, schedule: { kind: "daily", time: "03:00" }, time_zone: "UTC", permission_mode: "auto" });
// Long enough for a backlog to stay queued while the other person's run arrives
const LONGER = "Write two short paragraphs about lighthouses, no headings.";

const alan = mkdtempSync(join(tmpdir(), "gen9-fairness-alan-"));
const ada = mkdtempSync(join(tmpdir(), "gen9-fairness-ada-"));
const tasks = [];
const chats = [];
try {
  check(Boolean(await signInTerminal({ email: env.GEN9_SEED_USER_EMAIL, password: env.GEN9_SEED_USER_PASSWORD, configDir: alan })), "the seeded user signs in on the terminal");
  check(Boolean(await signInTerminal({ email: env.GEN9_SEED_ADMIN_EMAIL, password: env.GEN9_SEED_ADMIN_PASSWORD, configDir: ada })), "the seeded admin signs in on the terminal");
  const mine = (await api(alan, "POST", "/v1/tasks", task("e2e fairness user"))).body;
  const many = (await api(alan, "POST", "/v1/tasks", task("e2e fairness user many", LONGER))).body;
  const theirs = (await api(ada, "POST", "/v1/tasks", task("e2e fairness admin"))).body;
  tasks.push([alan, mine.id], [alan, many.id], [ada, theirs.id]);

  // One agent slot: the worker swapped for one that runs a single turn at a time
  docker("stop", WORKER);
  docker("rm", "-f", ONE_SLOT);
  const run = docker("compose", "-f", `${ROOT}gen9-agent/compose.yaml`, "run", "-d", "--no-deps", "--name", ONE_SLOT, "-e", "WORKER_CONCURRENCY=1", "worker");
  check(run.status === 0 && (await healthy(ONE_SLOT)), "a worker with one agent slot runs in its place", run.stderr.trim().split("\n").at(-1));

  // 1. Priority: a chat run goes before queued background runs
  for (let i = 0; i < 3; i++) await api(alan, "POST", `/v1/tasks/${mine.id}/run`);
  for (let i = 0; i < 30 && runsOf(mine.id).length < 3; i++) await sleep(500);
  const thread = (await api(ada, "POST", "/v1/threads")).body;
  chats.push([ada, thread.id]);
  await api(ada, "POST", `/v1/threads/${thread.id}/runs`, { message: "Reply with the single word: ready" });
  await allDone([mine.id], [thread.id]);
  const background = runsOf(mine.id).map((r) => r[2]);
  const chat = startedAt(thread.id);
  const later = background.slice(1).filter((t) => t > chat).length;
  check(later >= 1 && chat > background[0], "the chat run started before the queued background runs", `background ${background.join(", ")}; chat ${chat}`);

  // 2. Fairness: one person's many background runs don't hold back another's. The claim is about
  // the user's runs still queued when the admin's arrives: at least one of them starts after it
  for (let i = 0; i < 6; i++) await api(alan, "POST", `/v1/tasks/${many.id}/run`);
  for (let i = 0; i < 40 && runsOf(many.id).length < 6; i++) await sleep(500);
  await api(ada, "POST", `/v1/tasks/${theirs.id}/run`);
  await allDone([many.id, theirs.id]);
  const users = runsOf(many.id);
  const [[, , otherStarted, otherQueued]] = runsOf(theirs.id);
  const waiting = users.filter(([, , started, queued]) => queued < otherQueued && started > otherQueued);
  const overtaken = waiting.filter(([, , started]) => started > otherStarted);
  const shown = `user (queued→started) ${users.map((r) => `${r[3]}→${r[2]}`).join(", ")}; admin ${otherQueued}→${otherStarted}`;
  if (waiting.length < 2) check(false, "a backlog of the user's runs was still queued when the admin's arrived (else the test proves nothing)", shown);
  else check(overtaken.length >= 1, `the admin's background run didn't wait for all ${waiting.length} of the user's still queued`, shown);
} catch (e) {
  check(false, "the run finished", e.stack ?? String(e));
} finally {
  docker("rm", "-f", ONE_SLOT);
  docker("start", WORKER);
  check(await healthy(WORKER), "the worker is back, as it was");
  for (const [who, id] of tasks) {
    for (const [threadId] of runsOf(id)) await api(who, "DELETE", `/v1/threads/${threadId}`).catch(() => {});
    await api(who, "DELETE", `/v1/tasks/${id}`).catch(() => {});
  }
  for (const [who, id] of chats) await api(who, "DELETE", `/v1/threads/${id}`).catch(() => {});
  for (const dir of [alan, ada]) rmSync(dir, { recursive: true, force: true });
}
console.log(failures ? `\n${failures} check(s) failed` : "\nall priority and fairness checks passed");
process.exit(failures ? 1 : 0);
