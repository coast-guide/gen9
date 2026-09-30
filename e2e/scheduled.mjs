// Scheduled tasks (gen9-agent/README.md, "Scheduled tasks"; docs/design/screens/scheduled.md), as
// the seeded user in Chrome on /scheduled, with Temporal's view read from inside the worker:
//   1. a one-off set two minutes ahead fires by itself (a delayed start): its chat answers with
//      the task's unguessable phrase, and the task says Done
//   2. an hourly task fires by its own Temporal Schedule at its minute; Run now makes another chat;
//      Pause and Resume pause and resume the Schedule; Edit renames it
//   3. the screen has no serious accessibility violations; another person sees none of it
//   4. in the terminal: gen9 tasks lists it; add and delete work
//   5. Delete (after a confirm) removes the Schedule and keeps the chats
// It deletes its chats at the end, and costs a few short replies. Needs every stack up.
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
const AXE = readFileSync(createRequire(import.meta.url).resolve("axe-core/axe.min.js"), "utf8");
const PHRASE = `orchard-${randomBytes(4).toString("hex")}`;

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
    let out = "";
    child.stdout.on("data", (d) => (out += d));
    child.stderr.on("data", (d) => (out += d));
    child.on("exit", (code) => resolve({ code, out }));
    child.stdin.end();
  });
async function api(configDir, method, path, body) {
  await gen9(configDir, ["whoami"]);
  const token = JSON.parse(readFileSync(join(configDir, "credentials.json"), "utf8")).access_token;
  const response = await fetch(`${API}${path}`, { method, headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const text = await response.text();
  return { status: response.status, body: text ? JSON.parse(text) : null };
}
// Temporal's view of a task's Schedule, from inside the worker (which holds the connection)
const scheduleOf = (taskId) =>
  execFileSync("docker", ["exec", "gen9-agent-worker-1", "python", "-c", `
import asyncio, httpx
from temporalio.service import RPCError
from gen9_agent.settings import Settings
from gen9_agent.temporal import connect
from gen9_agent.keycloak_admin import KeycloakAdmin
async def main():
    s = Settings()
    async with httpx.AsyncClient() as http:
        kc = KeycloakAdmin(s, http) if s.keycloak_admin_client_secret else None
        client = await connect(s, "e2e-scheduled", kc)
        try:
            d = await client.get_schedule_handle("task-${taskId}").describe()
            print("paused" if d.schedule.state.paused else "active", d.info.num_actions)
        except RPCError:
            print("missing")
asyncio.run(main())`], { encoding: "utf8" }).trim();
const threadsOf = (taskId) => psql(`select coalesce(string_agg(t.id::text, ',' order by t.created_at), '') from threads t where t.task_id = '${taskId}'`).split(",").filter(Boolean);
const finishedRuns = (taskId) => psql(`select count(*) from runs r join threads t on t.id = r.thread_id where t.task_id = '${taskId}' and r.status = 'success'`);
async function until(what, test, seconds) {
  for (let i = 0; i < seconds; i++) {
    const got = await test();
    if (got) return got;
    await sleep(1000);
  }
  throw new Error(`${what} didn't happen in ${seconds} s`);
}
// A React-controlled input set as a person's typing would
const setValue = (page, selector, value) =>
  page.$eval(
    selector,
    (el, v) => {
      const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), "value").set;
      setter.call(el, v);
      el.dispatchEvent(new Event("input", { bubbles: true }));
    },
    value,
  );
const pad = (n) => String(n).padStart(2, "0");

const alan = mkdtempSync(join(tmpdir(), "gen9-scheduled-alan-"));
const ada = mkdtempSync(join(tmpdir(), "gen9-scheduled-ada-"));
const browser = await launch({ headless: !process.env.HEADED, defaultViewport: { width: 1280, height: 900 } });
const page = await browser.newPage();
const made = [];
async function newTask(fields) {
  await page.goto(`${APP}/scheduled`, { waitUntil: "networkidle0" });
  for (const b of await page.$$("main button")) if ((await b.evaluate((e) => e.textContent.trim())) === "New task") await b.click();
  await page.waitForSelector("#new-name");
  await page.type("#new-name", fields.name);
  await page.type("#new-prompt", fields.prompt);
  await page.select("#new-kind", fields.kind);
  if (fields.time) await setValue(page, "#new-time", fields.time);
  if (fields.minute !== undefined) await setValue(page, "#new-minute", String(fields.minute));
  await page.select("#new-mode", "auto");
  await page.click('form[aria-label="New task"] button[type="submit"]');
  await page.waitForFunction((n) => [...document.querySelectorAll('ul[aria-label="Scheduled tasks"] li')].some((li) => li.textContent.includes(n)) || document.querySelector('form [role="alert"]'), { timeout: 20_000 }, fields.name);
  const refused = await page.$eval('form [role="alert"]', (e) => e.textContent).catch(() => "");
  const id = psql(`select id from tasks where name = '${fields.name}' order by created_at desc limit 1`);
  if (id) made.push(id);
  return { id, refused };
}
async function menu(name, item) {
  await page.goto(`${APP}/scheduled`, { waitUntil: "networkidle0" });
  await page.click(`button[aria-label="Actions for ${name}"]`);
  await page.waitForSelector("[role=menuitem]");
  for (const el of await page.$$("[role=menuitem]")) if ((await el.evaluate((e) => e.textContent.trim())) === item) return el.click();
  throw new Error(`no ${item} for ${name}`);
}
const rowText = async (name) => (await page.$$eval('ul[aria-label="Scheduled tasks"] > li', (lis, n) => lis.map((li) => li.textContent).find((t) => t.includes(n)) ?? "", name));

try {
  await page.goto(`${APP}/auth/login?returnTo=/scheduled`, { waitUntil: "networkidle0" });
  await page.type("#username", env.GEN9_SEED_USER_EMAIL);
  await page.type("#password", env.GEN9_SEED_USER_PASSWORD);
  await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }), page.click("#kc-login")]);
  check((await page.$eval("h1", (e) => e.textContent)) === "Scheduled", "the seeded user opens Scheduled");
  check(Boolean(await signInTerminal({ email: env.GEN9_SEED_USER_EMAIL, password: env.GEN9_SEED_USER_PASSWORD, configDir: alan })), "the seeded user signs in on the terminal");
  check(Boolean(await signInTerminal({ email: env.GEN9_SEED_ADMIN_EMAIL, password: env.GEN9_SEED_ADMIN_PASSWORD, configDir: ada })), "the seeded admin signs in on the terminal");
  for (const t of (await api(alan, "GET", "/v1/tasks")).body ?? []) if (t.name.startsWith("e2e")) await api(alan, "DELETE", `/v1/tasks/${t.id}`);

  // 1. A one-off, two minutes ahead: a delayed start
  const soon = new Date(Date.now() + 120_000);
  const once = await newTask({ name: "e2e once", prompt: `Reply with exactly this and nothing else: ${PHRASE}`, kind: "once", time: `${pad(soon.getHours())}:${pad(soon.getMinutes())}` });
  if (!check(once.id && !once.refused, "a one-off two minutes ahead is scheduled", once.refused)) throw new Error(`not scheduled: ${once.refused}`);
  check(/Next: in (1|2) minutes?/.test(await rowText("e2e once")), "its row says when it runs next", (await rowText("e2e once")).slice(0, 120));
  await until("the one-off's run", () => finishedRuns(once.id) === "1", 360);
  const [onceChat] = threadsOf(once.id);
  const onceThread = (await api(alan, "GET", `/v1/threads/${onceChat}`)).body;
  const onceAnswer = onceThread.messages.filter((m) => m.role === "assistant").at(-1)?.content ?? "";
  check(onceAnswer.includes(PHRASE) && onceThread.title === "e2e once", "it fired by itself, in a chat of its own named after it, and answered with the task's message", onceAnswer.slice(0, 80));
  await page.goto(`${APP}/scheduled`, { waitUntil: "networkidle0" });
  check(/Done/.test(await rowText("e2e once")) && psql(`select status from tasks where id = '${once.id}'`) === "done", "the one-off says Done, its run linked", (await rowText("e2e once")).slice(0, 140));

  // 2. Every hour, at a minute two ahead: the Schedule fires it
  const next = new Date(Date.now() + 120_000);
  const hourly = await newTask({ name: "e2e hourly", prompt: "Reply with the single word: ready", kind: "hourly", minute: next.getMinutes() });
  if (!hourly.id) throw new Error(`not scheduled: ${hourly.refused}`);
  check(!hourly.refused && /^active/.test(scheduleOf(hourly.id)), "an hourly task is a Temporal Schedule", scheduleOf(hourly.id));
  check(/Every hour at :\d\d/.test(await rowText("e2e hourly")), "its row says when, in words", (await rowText("e2e hourly")).slice(0, 120));
  await until("the Schedule's firing", () => finishedRuns(hourly.id) === "1", 420);
  check(scheduleOf(hourly.id) === "active 1", "its Schedule fired it once, at its minute", scheduleOf(hourly.id));
  await menu("e2e hourly", "Run now");
  await until("Run now's run", () => finishedRuns(hourly.id) === "2", 240);
  check(threadsOf(hourly.id).length === 2, "Run now made another chat", `${threadsOf(hourly.id).length} chats`);
  await menu("e2e hourly", "Pause");
  await until("the pause", () => scheduleOf(hourly.id).startsWith("paused"), 20);
  await page.goto(`${APP}/scheduled`, { waitUntil: "networkidle0" });
  check(/Paused/.test(await rowText("e2e hourly")) && !/Next:/.test(await rowText("e2e hourly")), "Pause pauses its Schedule; the row says Paused, with no next run", scheduleOf(hourly.id));
  await menu("e2e hourly", "Resume");
  await until("the resume", () => scheduleOf(hourly.id).startsWith("active"), 20);
  check(true, "Resume resumes it");
  await menu("e2e hourly", "Edit");
  const editName = `[id="${hourly.id}-name"]`;
  await page.waitForSelector(editName);
  await page.click(editName, { count: 3 });
  await page.type(editName, "e2e hourly edited");
  await page.click(`form[aria-label="Edit e2e hourly"] button[type="submit"]`);
  await page.waitForFunction(() => [...document.querySelectorAll('ul[aria-label="Scheduled tasks"] li')].some((li) => li.textContent.includes("e2e hourly edited")), { timeout: 20_000 });
  check(/^active/.test(scheduleOf(hourly.id)), "Edit renames it, and its Schedule stays", scheduleOf(hourly.id));

  // 3. Accessibility, and another person
  await page.goto(`${APP}/scheduled`, { waitUntil: "networkidle0" });
  await page.evaluate(AXE);
  const { violations } = await page.evaluate(() => window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] } }));
  const blocking = violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  check(!blocking.length, "Scheduled has no serious accessibility violations", blocking.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`).join("; "));
  const theirs = await api(ada, "GET", "/v1/tasks");
  const touch = await api(ada, "POST", `/v1/tasks/${hourly.id}/run`);
  check(theirs.status === 200 && !theirs.body.some((t) => t.name.startsWith("e2e")) && touch.status === 404, "another person sees none of it, and can't run it", `${touch.status}`);

  // 4. The terminal
  const listed = await gen9(alan, ["tasks"]);
  check(/e2e hourly edited/.test(listed.out) && /Every hour at/.test(listed.out), "gen9 tasks lists it", listed.out.split("\n").slice(0, 3).join(" | "));
  const added = await gen9(alan, ["tasks", "add", "e2e cli", "Say hi", "--every", "week", "--on", "fri", "--at", "07:30"]);
  const cliId = psql("select id from tasks where name = 'e2e cli'");
  if (cliId) made.push(cliId);
  check(added.code === 0 && /Every Friday at 07:30/.test(added.out) && /^active/.test(scheduleOf(cliId)), "gen9 tasks add schedules one", added.out.trim().split("\n").slice(0, 2).join(" | "));
  const deleted = await gen9(alan, ["tasks", "delete", "e2e cli"]);
  check(deleted.code === 0 && scheduleOf(cliId) === "missing", "gen9 tasks delete removes it", deleted.out.trim());

  // 5. Delete
  const chats = threadsOf(hourly.id);
  await menu("e2e hourly edited", "Delete…");
  const dialog = await page.waitForSelector('[role="alertdialog"]');
  const asked = await dialog.evaluate((d) => d.textContent);
  for (const b of await page.$$('[role="alertdialog"] button')) if ((await b.evaluate((e) => e.textContent.trim())) === "Delete") await b.click();
  await until("the deletion", () => psql(`select count(*) from tasks where id = '${hourly.id}'`) === "0", 20);
  const kept = psql(`select count(*) from threads where id in (${chats.map((c) => `'${c}'`).join(",")}) and deleted_at is null`);
  check(/chats it made stay/.test(asked) && scheduleOf(hourly.id) === "missing" && kept === String(chats.length), "Delete asks first, removes its Schedule, and keeps its chats", `${scheduleOf(hourly.id)}; ${kept} of ${chats.length} chats kept`);
  for (const chat of [...chats, onceChat]) await api(alan, "DELETE", `/v1/threads/${chat}`);
} catch (e) {
  check(false, "the run finished", e.stack ?? String(e));
} finally {
  for (const id of made) await api(alan, "DELETE", `/v1/tasks/${id}`).catch(() => {});
  await browser.close();
  for (const dir of [alan, ada]) rmSync(dir, { recursive: true, force: true });
}
console.log(failures ? `\n${failures} check(s) failed` : "\nall scheduled task checks passed");
process.exit(failures ? 1 : 0);
