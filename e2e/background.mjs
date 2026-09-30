// Background tasks (gen9-agent/README.md, "Background tasks"; docs/design/screens/chat.md), as
// Deep Agents' async subagents, on Gen9's runs. As the seeded user:
//   1. asked to, the agent starts a task in the background and says so; the task is a chat of its
//      own (not in the sidebar's list) running as the person, at background priority
//   2. while the task works, the chat answers another message
//   3. asked to check, the agent returns the task's answer (an unguessable phrase only the task's
//      description holds)
//   4. the chat's step opens the task's chat, which shows its work and takes no new messages;
//      axe clean
//   5. a task that finishes on its own tells its chat: a turn of its own, marked as Gen9's,
//      which an open page follows without a reload; a chat busy with a run gets it after
//   6. a task in "Ask before acting" that needs Allow asks in the chat that started it (the
//      sidebar says "Needs you"); Allow there lets it go on; another person can't answer
//   7. cancel stops a task, and update gives it new instructions in its own chat; past the
//      per-chat limit, start refuses; deleting the chat deletes its tasks' chats
// It deletes its chats at the end, and costs a few short replies.
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
const PHRASE = `kumquat-${randomBytes(3).toString("hex")}`;

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
async function until(what, test, seconds) {
  for (let i = 0; i < seconds; i++) {
    const got = await test();
    if (got) return got;
    await sleep(1000);
  }
  throw new Error(`${what} didn't happen in ${seconds} s`);
}
const latest = (threadId) => psql(`select coalesce((select status from runs where thread_id = '${threadId}' order by created_at desc limit 1), '')`);
const idle = (threadId) => ["", "success", "error", "cancelled", "expired"].includes(latest(threadId));
// Sends a message in the chat once it's idle (a task's notice may be answering in it) and waits
// for its run to finish
async function say(configDir, threadId, message) {
  let sent;
  for (let i = 0; i < 90; i++) {
    await until("the chat to be idle", () => idle(threadId), 240);
    sent = await api(configDir, "POST", `/v1/threads/${threadId}/runs`, { message, permission_mode: "auto" });
    if (sent.status !== 409) break;
    await sleep(2000);
  }
  if (sent.status !== 202) throw new Error(`message refused: ${sent.status} ${JSON.stringify(sent.body)}`);
  await until("the answer", () => ["success", "error", "cancelled", "expired"].includes(latest(threadId)), 240);
  const thread = (await api(configDir, "GET", `/v1/threads/${threadId}`)).body;
  return { thread, answer: thread.messages.filter((m) => m.role === "assistant").at(-1)?.content ?? "" };
}
// The priority and fairness key Temporal holds for a run's workflow, from inside the worker
const priorityOf = (runId) =>
  execFileSync("docker", ["exec", "gen9-agent-worker-1", "python", "-c", `
import asyncio, httpx
from gen9_agent.settings import Settings
from gen9_agent.temporal import connect
from gen9_agent.keycloak_admin import KeycloakAdmin
async def main():
    s = Settings()
    async with httpx.AsyncClient() as http:
        kc = KeycloakAdmin(s, http) if s.keycloak_admin_client_secret else None
        client = await connect(s, "e2e-background", kc)
        d = await client.get_workflow_handle("run-${runId}").describe()
        p = d.raw_description.workflow_execution_info.priority
        print(p.priority_key, p.fairness_key)
asyncio.run(main())`], { encoding: "utf8" }).trim();
const tasksOf = (threadId) => psql(`select coalesce(string_agg(id::text, ',' order by created_at), '') from threads where parent_id = '${threadId}'`).split(",").filter(Boolean);

const alan = mkdtempSync(join(tmpdir(), "gen9-background-alan-"));
const ada = mkdtempSync(join(tmpdir(), "gen9-background-ada-"));
const browser = await launch({ headless: !process.env.HEADED, defaultViewport: { width: 1280, height: 900 } });
const page = await browser.newPage();
const chats = [];
try {
  await page.goto(`${APP}/auth/login?returnTo=/chat`, { waitUntil: "networkidle0" });
  await page.type("#username", env.GEN9_SEED_USER_EMAIL);
  await page.type("#password", env.GEN9_SEED_USER_PASSWORD);
  await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }), page.click("#kc-login")]);
  check(Boolean(await signInTerminal({ email: env.GEN9_SEED_USER_EMAIL, password: env.GEN9_SEED_USER_PASSWORD, configDir: alan })), "the seeded user signs in on the terminal");
  check(Boolean(await signInTerminal({ email: env.GEN9_SEED_ADMIN_EMAIL, password: env.GEN9_SEED_ADMIN_PASSWORD, configDir: ada })), "the seeded admin signs in on the terminal");

  // 1. Start a task in the background
  const chat = (await api(alan, "POST", "/v1/threads")).body;
  chats.push(chat.id);
  const started = await say(
    alan,
    chat.id,
    `Start a background task (agent type gen9) with exactly this description: "Wait a moment, then reply with only this code word and nothing else: ${PHRASE}". Then tell me you started it. Don't check it yet.`,
  );
  const [task] = tasksOf(chat.id);
  const step = started.thread.messages.flatMap((m) => m.steps ?? []).find((s) => s.name === "start_async_task");
  check(Boolean(task) && step?.task === task && !started.answer.includes(PHRASE), "the agent starts a task in the background: a chat of its own, linked from its step", `${task} ${step?.task}`);
  const listed = (await api(alan, "GET", "/v1/threads")).body.map((t) => t.id);
  check(listed.includes(chat.id) && !listed.includes(task), "the sidebar's list shows the chat, not the task's chat");
  check(psql(`select u.email from threads t join users u on u.id = t.user_id where t.id = '${task}'`) === env.GEN9_SEED_USER_EMAIL && psql(`select parent_id from threads where id = '${task}'`) === chat.id, "the task's chat is the person's, and keeps the chat that started it");
  check(started.thread.tasks?.length === 1 && started.thread.tasks[0].id === task, "the chat lists its task", JSON.stringify(started.thread.tasks?.[0] ?? null));
  const taskRun = psql(`select id from runs where thread_id = '${task}' order by created_at limit 1`);
  const priority = priorityOf(taskRun);
  check(priority === `3 ${psql(`select sub from users where email = '${env.GEN9_SEED_USER_EMAIL}'`)}`, "the task runs at background priority, with the person as fairness key", priority.split(" ")[0]);

  // 2. The chat answers meanwhile
  const meanwhile = await say(alan, chat.id, "Meanwhile: what is 17 times 3? Reply with the number only.");
  check(/51/.test(meanwhile.answer), "the chat answers another message meanwhile", meanwhile.answer.slice(0, 40));

  // 3. Check returns its answer
  await until("the task to finish", () => latest(task) === "success", 240);
  const checked = await say(alan, chat.id, "Check the background task now and tell me exactly what it answered.");
  check(checked.answer.includes(PHRASE), "check returns the task's answer", checked.answer.slice(0, 80));
  const refused = await api(alan, "POST", `/v1/threads/${task}/runs`, { message: "hello" });
  check(refused.status === 409, "the task's chat takes no messages of its own", `${refused.status}`);
  const theirs = await api(ada, "GET", `/v1/threads/${task}`);
  check(theirs.status === 404, "another person gets 404 on the task's chat", `${theirs.status}`);

  // 4. In Chrome: the chat's list and step, and the task's chat
  await page.goto(`${APP}/chat/${chat.id}`, { waitUntil: "networkidle0" });
  const list = await page.$eval('section[aria-label="In the background"]', (s) => s.textContent).catch(() => "");
  check(list.includes(PHRASE.slice(0, 8)) && /Done/.test(list), "the chat shows its task “In the background”, with its state", list.slice(0, 120));
  const opens = await page.$$eval(`a[href="/chat/${task}"]`, (as) => as.map((a) => a.textContent.trim()));
  check(opens.includes("Open its chat"), "the step that started it opens the task's chat", opens.join(" | "));
  await page.evaluate(AXE);
  let { violations } = await page.evaluate(() => window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] } }));
  let blocking = violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  check(!blocking.length, "the chat with its task has no serious accessibility violations", blocking.map((v) => v.id).join(", "));
  await page.goto(`${APP}/chat/${task}`, { waitUntil: "networkidle0" });
  const note = await page.$eval("main", (m) => m.textContent);
  check(note.includes("A background task") && note.includes(PHRASE) && !(await page.$("#composer")) && Boolean(await page.$(`a[href="/chat/${chat.id}"]`)), "the task's chat shows its work, links back, and has no composer");
  await page.evaluate(AXE);
  ({ violations } = await page.evaluate(() => window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] } })));
  blocking = violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  check(!blocking.length, "the task's chat has no serious accessibility violations", blocking.map((v) => v.id).join(", "));

  // 5. A finished task tells its chat: a turn of its own, followed by the open page
  const told = (await api(alan, "POST", "/v1/threads")).body;
  chats.push(told.id);
  const PHRASE3 = `medlar-${randomBytes(3).toString("hex")}`;
  await say(alan, told.id, `Start a background task (agent type gen9) with this description: "Write two sentences about lighthouses, then a last line with only this code word: ${PHRASE3}". Then say you started it. Don't check it.`);
  await page.goto(`${APP}/chat/${told.id}`, { waitUntil: "networkidle0" });
  const [toldTask] = tasksOf(told.id);
  await page.waitForFunction((phrase) => document.querySelector("main")?.textContent.includes("From a background task") && [...document.querySelectorAll("main li")].some((li) => !li.textContent.includes("From a background task") && li.textContent.includes(phrase)), { timeout: 240_000 }, PHRASE3).catch(() => {});
  const shown = await page.$eval("main", (m) => m.textContent);
  const noticeRuns = psql(`select count(*) from runs where thread_id = '${told.id}' and input ->> 'notice_of' = (select id::text from runs where thread_id = '${toldTask}' order by created_at desc limit 1)`);
  check(shown.includes("From a background task") && shown.includes(PHRASE3) && noticeRuns === "1", "a finished task tells its chat: one notice, a turn of its own, which the open page shows without a reload", `${noticeRuns} notice run(s)`);
  const toldThread = (await api(alan, "GET", `/v1/threads/${told.id}`)).body;
  const noticeMessage = toldThread.messages.find((m) => m.notice);
  check(Boolean(noticeMessage) && !toldThread.messages.filter((m) => m.role === "user" && !m.notice).some((m) => m.content.startsWith("[Background task")) && toldThread.tasks[0].told, "the notice is marked as Gen9's, not the person's; the task counts as told");
  await page.evaluate(AXE);
  ({ violations } = await page.evaluate(() => window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] } })));
  blocking = violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  check(!blocking.length, "the chat with a notice has no serious accessibility violations", blocking.map((v) => v.id).join(", "));
  // A chat busy with a run of its own when the task ends gets the notice after
  await until("the notice's answer", () => idle(told.id), 180);
  const PHRASE4 = `sloe-${randomBytes(3).toString("hex")}`;
  // One turn starts the task, then writes a long story: the chat is busy while the task ends
  await until("the chat to be idle", () => idle(told.id), 240);
  const busy = await api(alan, "POST", `/v1/threads/${told.id}/runs`, {
    message: `First start another background task (agent type gen9) with this description: "Reply with only this code word: ${PHRASE4}". Right after starting it, in this same reply, write a 1200-word story about a lighthouse keeper. Don't check the task.`,
    permission_mode: "auto",
  });
  if (busy.status !== 202) throw new Error(`the story was refused: ${busy.status}`);
  const story = busy.body?.id ?? busy.body?.run_id;
  await until("the second task", () => tasksOf(told.id).length === 2, 120);
  const second = tasksOf(told.id).find((t) => t !== toldTask);
  await until("the second task to finish", () => latest(second ?? "") === "success", 240);
  await until("its notice", () => psql(`select count(*) from runs where thread_id = '${told.id}' and input ->> 'notice_of' = (select id::text from runs where thread_id = '${second}' order by created_at desc limit 1)`) === "1", 300);
  // The task ended while the story was being written, and its notice came after the story
  const [whileBusy, after] = psql(`select (t.finished_at < s.finished_at)::text || ' ' || (n.created_at >= s.finished_at)::text from runs t, runs s, runs n where s.id = '${story}' and t.id = (select id from runs where thread_id = '${second}' order by created_at desc limit 1) and n.thread_id = '${told.id}' and n.input ->> 'notice_of' = t.id::text`).split(" ");
  check(whileBusy === "true" && after === "true", "a chat busy when its task ends gets the notice after its own run", `task ended during the story: ${whileBusy}; notice after it: ${after}`);
  await until("the notice's answer", () => latest(told.id) === "success", 180);

  // 6. A task that needs Allow asks in its chat: answered there, it goes on
  const asking = (await api(alan, "POST", "/v1/threads")).body;
  chats.push(asking.id);
  const BIRD = `kingfisher-${randomBytes(3).toString("hex")}`;
  const memoryBefore = (await api(alan, "GET", "/v1/me/memory")).body?.content ?? "";
  const startedAsking = await api(alan, "POST", `/v1/threads/${asking.id}/runs`, {
    message: `Start a background task (agent type gen9) with this description: "Save to my memory that my favourite bird is the ${BIRD}, then reply with only: remembered". Then say you started it.`,
    permission_mode: "ask",
  });
  if (startedAsking.status !== 202) throw new Error(`refused: ${startedAsking.status}`);
  await until("the chat's turn", () => idle(asking.id), 180);
  const [askingTask] = tasksOf(asking.id);
  await until("the task to ask", () => latest(askingTask ?? "") === "waiting", 180);
  await page.goto(`${APP}/chat/${asking.id}`, { waitUntil: "networkidle0" });
  const card = await page.waitForSelector('section[aria-label="In the background"] section[aria-label="Gen9 needs your approval"]', { timeout: 20_000 }).catch(() => null);
  const sidebar = await page.$$eval(`a[href="/chat/${asking.id}"]`, (as) => as.map((a) => a.closest("li")?.textContent ?? "").join(" "));
  check(Boolean(card) && /Needs you/.test(sidebar), "a task that needs Allow asks in its chat, which the sidebar marks “Needs you”", sidebar.slice(0, 80));
  await page.evaluate(AXE);
  ({ violations } = await page.evaluate(() => window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] } })));
  blocking = violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  check(!blocking.length, "the chat with a task's approval has no serious accessibility violations", blocking.map((v) => v.id).join(", "));
  const pending = (await api(alan, "GET", `/v1/threads/${asking.id}/tasks`)).body[0];
  const theirAnswer = await api(ada, "POST", `/v1/threads/${askingTask}/runs/${pending.run_id}/inputs/${pending.requests[0]?.id}`, { decisions: [{ type: "approve" }] });
  check(theirAnswer.status === 404 && pending.requests[0]?.kind === "approval", "another person can't answer it", `${theirAnswer.status}`);
  for (const b of await card.$$("button")) if ((await b.evaluate((e) => e.textContent.trim())) === "Allow") await b.click();
  await until("the task to finish", () => latest(askingTask) === "success", 180);
  const remembered = (await api(alan, "GET", "/v1/me/memory")).body?.content ?? "";
  await until("its notice", () => psql(`select count(*) from runs where thread_id = '${asking.id}' and input ? 'notice_of'`) === "1", 180);
  check(remembered.includes(BIRD), "Allow in the chat lets the task go on: it wrote the memory, and its notice follows");
  await api(alan, "PUT", "/v1/me/memory", { content: memoryBefore });

  // 7. Cancel, the limit, deletion
  await say(alan, chat.id, "Start another background task (agent type gen9) with this description: \"Write a detailed 2000-word essay on the history of the teapot, with sections.\" Then say you started it.");
  const long = tasksOf(chat.id).find((t) => t !== task);
  await until("the second task to start working", () => ["running", "success"].includes(latest(long ?? "")), 60);
  await say(alan, chat.id, "Cancel that second background task now, then tell me you did.");
  await until("the second task to stop", () => ["cancelled", "success"].includes(latest(long)), 60);
  check(latest(long) === "cancelled", "cancel stops a task", latest(long));
  const PHRASE2 = `quince-${randomBytes(3).toString("hex")}`;
  await say(alan, chat.id, `Give that second background task new instructions (update it): "Reply with only this code word: ${PHRASE2}". Then say you did.`);
  await until("the updated task to finish", () => latest(long) === "success", 180);
  const updated = (await api(alan, "GET", `/v1/threads/${long}`)).body;
  const asks = updated.messages.filter((m) => m.role === "user");
  check(asks.length === 2 && updated.messages.filter((m) => m.role === "assistant").at(-1)?.content.includes(PHRASE2), "update sends new instructions in the task's own chat, which answers them", `${asks.length} messages`);
  // Four tasks not yet started fill the chat's limit (rows only: no model spend)
  psql(`insert into threads (user_id, parent_id, title) select user_id, id, 'placeholder' from threads, generate_series(1, 4) where id = '${chat.id}'`);
  const before = tasksOf(chat.id).length;
  const limited = await say(alan, chat.id, "Start one more background task (agent type gen9): \"Say hi.\" Tell me exactly what the tool answered.");
  check(tasksOf(chat.id).length === before && /at most 4/i.test(limited.answer), "past 4 unfinished tasks in a chat, start refuses", limited.answer.slice(0, 100));
  const all = tasksOf(chat.id);
  const gone = await api(alan, "DELETE", `/v1/threads/${chat.id}`);
  // Deleted here: the rest of `chats` (the notice and approval chats) are deleted at the end
  chats.splice(chats.indexOf(chat.id), 1);
  await until("the task chats to be deleted", () => psql(`select count(*) from threads where id in (${all.map((t) => `'${t}'`).join(",")})`) === "0", 120);
  check([202, 204].includes(gone.status), "deleting the chat deletes its tasks' chats", `${gone.status}`);
} catch (e) {
  check(false, "the run finished", e.stack ?? String(e));
} finally {
  for (const id of chats) await api(alan, "DELETE", `/v1/threads/${id}`).catch(() => {});
  await browser.close();
  for (const dir of [alan, ada]) rmSync(dir, { recursive: true, force: true });
}
console.log(failures ? `\n${failures} check(s) failed` : "\nall background task checks passed");
process.exit(failures ? 1 : 0);
