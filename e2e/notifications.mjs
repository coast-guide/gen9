// Notices about background runs (gen9-agent/README.md, "Scheduled tasks"; docs/design/screens/
// settings.md, "Notifications"), read from the Keycloak stack's Mailpit (SMTP_URL in development).
// As the seeded user:
//   1. a task's run that finishes sends one email, "<task> is done", linking its chat, without the
//      answer; a task's run that needs Allow sends "<task> needs you"
//   2. Settings > Notifications, "Only when a task needs me": a finished run sends nothing; "Never":
//      a run that needs Allow sends nothing; axe clean
//   3. a chat the person is in (not a task's) sends nothing
// It puts the setting back, deletes its tasks and chats, and costs a few short replies.
import { execFileSync, spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
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
const MAILPIT = process.env.MAILPIT_URL ?? "http://localhost:15002";
// Mailpit's API asks for its password (user gen9; MAILPIT_UI_PASSWORD in gen9-keycloak/.env)
const MAIL = { headers: { Authorization: `Basic ${Buffer.from(`gen9:${env.MAILPIT_UI_PASSWORD ?? ""}`).toString("base64")}` } };
const AXE = readFileSync(createRequire(import.meta.url).resolve("axe-core/axe.min.js"), "utf8");
const TAG = randomBytes(3).toString("hex");
const PHRASE = `quince-${TAG}`;

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
// Emails to the seeded user about this run's tasks (each task's name carries TAG)
async function mails(subject) {
  const found = await (await fetch(`${MAILPIT}/api/v1/search?query=${encodeURIComponent(`to:${env.GEN9_SEED_USER_EMAIL} subject:"${subject}"`)}`, MAIL)).json();
  return found.messages ?? [];
}
const body = async (id) => (await (await fetch(`${MAILPIT}/api/v1/message/${id}`, MAIL)).json()).Text ?? "";
const threadsOf = (taskId) => psql(`select coalesce(string_agg(id::text, ',' order by created_at), '') from threads where task_id = '${taskId}'`).split(",").filter(Boolean);
const statusOf = (threadId) => psql(`select coalesce(max(status), '') from runs where thread_id = '${threadId}'`);
async function until(what, test, seconds) {
  for (let i = 0; i < seconds; i++) {
    const got = await test();
    if (got) return got;
    await sleep(1000);
  }
  throw new Error(`${what} didn't happen in ${seconds} s`);
}
// Runs a task now; its newest chat once its run is done or waiting
async function runOnce(taskId, state) {
  const before = threadsOf(taskId).length;
  await api(alan, "POST", `/v1/tasks/${taskId}/run`);
  return until(`the task's run (${state})`, () => {
    const chats = threadsOf(taskId);
    return chats.length > before && statusOf(chats.at(-1)) === state ? chats.at(-1) : null;
  }, 180);
}
const task = (name, prompt, permission_mode) => ({ name, prompt, schedule: { kind: "daily", time: "03:00" }, time_zone: "UTC", permission_mode });

const alan = mkdtempSync(join(tmpdir(), "gen9-notices-alan-"));
const browser = await launch({ headless: !process.env.HEADED, defaultViewport: { width: 1280, height: 900 } });
const page = await browser.newPage();
const tasks = [];
const chats = [];
async function choose(value) {
  await page.goto(`${APP}/settings`, { waitUntil: "networkidle0" });
  // "Email me" is a radio group (P3-D1)
  await page.click(`fieldset input[type="radio"][name="email"][value="${value}"]`);
  await until("the choice saved", () => psql(`select notify from users where email = '${env.GEN9_SEED_USER_EMAIL}'`) === value, 20);
}
try {
  await page.goto(`${APP}/auth/login?returnTo=/settings`, { waitUntil: "networkidle0" });
  await page.type("#username", env.GEN9_SEED_USER_EMAIL);
  await page.type("#password", env.GEN9_SEED_USER_PASSWORD);
  await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }), page.click("#kc-login")]);
  check(Boolean(await signInTerminal({ email: env.GEN9_SEED_USER_EMAIL, password: env.GEN9_SEED_USER_PASSWORD, configDir: alan })), "the seeded user signs in on the terminal");
  await choose("all");
  const done = (await api(alan, "POST", "/v1/tasks", task(`e2e done ${TAG}`, `Reply with exactly this and nothing else: ${PHRASE}`, "auto"))).body;
  const asks = (await api(alan, "POST", "/v1/tasks", task(`e2e asks ${TAG}`, `Remember that my favourite fruit is ${PHRASE}.`, "ask"))).body;
  tasks.push(done.id, asks.id);

  // 1. Done, and needs you
  const doneChat = await runOnce(done.id, "success");
  const [doneMail] = await until("the done email", async () => {
    const m = await mails(`e2e done ${TAG} is done`);
    return m.length ? m : null;
  }, 30);
  const doneText = await body(doneMail.ID);
  check(doneText.includes(`${APP}/chat/${doneChat}`) && !doneText.includes(PHRASE), "a finished task run sends “… is done”, linking its chat, without the answer", doneText.split("\n")[0]);
  const askChat = await runOnce(asks.id, "waiting");
  chats.push(askChat);
  const [askMail] = await until("the needs-you email", async () => {
    const m = await mails(`e2e asks ${TAG} needs you`);
    return m.length ? m : null;
  }, 30);
  check((await body(askMail.ID)).includes(`/chat/${askChat}`), "a run that needs Allow sends “… needs you”");
  await sleep(3000);
  check((await mails(`e2e done ${TAG}`)).length === 1, "once: a single email for the finished run");

  // 2. The person's choice
  await choose("needs_you");
  // Scanned once "Saved." has arrived: while the toast fades in, its text is measured against the
  // page through the fade (1.33:1 at 80% opacity) and fails contrast, which the toast at rest passes
  await page
    .waitForFunction(() => {
      const toast = document.querySelector("[data-sonner-toast]");
      return toast && getComputedStyle(toast).opacity === "1";
    }, { timeout: 5000 })
    .catch(() => {});
  await page.evaluate(AXE);
  const { violations } = await page.evaluate(() => window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] } }));
  const blocking = violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  check(!blocking.length, "Settings with Notifications has no serious accessibility violations", blocking.map((v) => v.id).join(", "));
  await runOnce(done.id, "success");
  await sleep(5000);
  check((await mails(`e2e done ${TAG}`)).length === 1, "“Only when a task needs me”: a finished run sends nothing");
  await choose("never");
  chats.push(await runOnce(asks.id, "waiting"));
  await sleep(5000);
  check((await mails(`e2e asks ${TAG}`)).length === 1, "“Never”: a run that needs Allow sends nothing");

  // 3. A chat the person is in
  await choose("all");
  const before = (await (await fetch(`${MAILPIT}/api/v1/search?query=${encodeURIComponent(`to:${env.GEN9_SEED_USER_EMAIL}`)}`, MAIL)).json()).messages_count;
  const thread = (await api(alan, "POST", "/v1/threads")).body;
  chats.push(thread.id);
  await api(alan, "POST", `/v1/threads/${thread.id}/runs`, { message: "Reply with the single word: ready" });
  await until("the chat's run", () => statusOf(thread.id) === "success", 120);
  await sleep(4000);
  const after = (await (await fetch(`${MAILPIT}/api/v1/search?query=${encodeURIComponent(`to:${env.GEN9_SEED_USER_EMAIL}`)}`, MAIL)).json()).messages_count;
  check(after === before, "a chat the person is in sends nothing", `${before} → ${after}`);
} catch (e) {
  check(false, "the run finished", e.stack ?? String(e));
} finally {
  psql(`update users set notify = 'all' where email = '${env.GEN9_SEED_USER_EMAIL}'`);
  for (const id of tasks) {
    for (const chat of threadsOf(id)) await api(alan, "DELETE", `/v1/threads/${chat}`).catch(() => {});
    await api(alan, "DELETE", `/v1/tasks/${id}`).catch(() => {});
  }
  for (const chat of chats) await api(alan, "DELETE", `/v1/threads/${chat}`).catch(() => {});
  await browser.close();
  rmSync(alan, { recursive: true, force: true });
}
console.log(failures ? `\n${failures} check(s) failed` : "\nall notification checks passed");
process.exit(failures ? 1 : 0);
