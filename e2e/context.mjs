// The context budget (gen9-agent/README.md, "Context"): a chat that outgrows it is summarized, and
// the person is told. The worker is swapped for one with a small budget (CONTEXT_BUDGET_TOKENS=12000,
// `docker compose run`, as fairness.mjs swaps its slots), and restored at the end; nothing else
// should use the worker meanwhile. As the seeded user:
//   1. three long messages (the first holding a code word) outgrow the budget: a run records
//      `context.summarized`, and no summary text reaches an answer
//   2. the summary, read from the chat's checkpoint, keeps the code word; asked afterwards, the
//      answer has it too. The person's memory and past-chat search are off meanwhile (put back at
//      the end): the agent saved "a note to keep" to memory, which made the code word come back
//      without the summary (docs/plans/manual-e2e.md, P6-E3)
//   3. the chat shows "Earlier messages were summarized" on that turn, also after a reload; the
//      whole chat is still there; axe clean
// It deletes its chat, puts the person's controls back, and costs four short replies and their
// summaries.
import { execFileSync, spawn, spawnSync } from "node:child_process";
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
const WORKER = "gen9-agent-worker-1";
const SMALL = "gen9-agent-worker-small-context";
const CODE = `osprey-${randomBytes(3).toString("hex")}`;
// About 1,900 tokens of plain text, under the API's 8,000 characters a message
const FILLER = Array.from({ length: 150 }, (_, i) => `Line ${i}: the tide came in and went out.`).join(" ");

let failures = 0;
function check(ok, what, detail = "") {
  console.log(`${ok ? "ok  " : "FAIL"}  ${what}${detail ? `: ${detail}` : ""}`);
  if (!ok) failures++;
  return ok;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const psql = (sql) => execFileSync("docker", ["exec", "gen9-postgres-postgres-1", "psql", "-U", "postgres", "-d", "gen9_agent", "-tAc", sql], { encoding: "utf8" }).trim();
const docker = (...args) => spawnSync("docker", args, { encoding: "utf8" });
async function healthy(name) {
  for (let i = 0; i < 90; i++) {
    if (docker("inspect", "-f", "{{.State.Health.Status}}", name).stdout.trim() === "healthy") return true;
    await sleep(2000);
  }
  return false;
}
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
// The summary Deep Agents keeps in the chat's state, read with the worker's own code and settings
const SUMMARY_READER = (threadId) => `
import asyncio
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from gen9_agent.agent import checkpoint_pool
from gen9_agent.settings import DatabaseSettings

async def main():
    pool = checkpoint_pool(DatabaseSettings())
    await pool.open()
    try:
        saved = await AsyncPostgresSaver(pool).aget_tuple({"configurable": {"thread_id": "${threadId}", "checkpoint_ns": ""}})
        event = (saved.checkpoint["channel_values"].get("_summarization_event") if saved else None) or {}
        message = event.get("summary_message") if isinstance(event, dict) else getattr(event, "summary_message", None)
        print(getattr(message, "content", "") or "")
    finally:
        await pool.close()

asyncio.run(main())
`;
const latest = (threadId) => psql(`select coalesce((select status from runs where thread_id = '${threadId}' order by created_at desc limit 1), '')`);
async function say(configDir, threadId, message) {
  const sent = await api(configDir, "POST", `/v1/threads/${threadId}/runs`, { message, permission_mode: "auto" });
  if (sent.status !== 202) throw new Error(`message refused: ${sent.status}`);
  for (let i = 0; i < 240 && !["success", "error", "cancelled", "expired"].includes(latest(threadId)); i++) await sleep(1000);
  return (await api(configDir, "GET", `/v1/threads/${threadId}`)).body;
}

const alan = mkdtempSync(join(tmpdir(), "gen9-context-alan-"));
const browser = await launch({ headless: !process.env.HEADED, defaultViewport: { width: 1280, height: 900 } });
const page = await browser.newPage();
let chat;
let controls;
try {
  check(Boolean(await signInTerminal({ email: env.GEN9_SEED_USER_EMAIL, password: env.GEN9_SEED_USER_PASSWORD, configDir: alan })), "the seeded user signs in on the terminal");
  controls = (await api(alan, "GET", "/v1/me/controls")).body;
  const off = await api(alan, "PUT", "/v1/me/controls", { remember: false, search_past_chats: false });
  check(off.status === 200 && off.body.remember === false && off.body.search_past_chats === false, "the person's memory and past-chat search are off for the check, so only the summary can carry the code word", JSON.stringify(off.body));
  docker("stop", WORKER);
  docker("rm", "-f", SMALL);
  const run = docker("compose", "-f", `${ROOT}gen9-agent/compose.yaml`, "run", "-d", "--no-deps", "--name", SMALL, "-e", "CONTEXT_BUDGET_TOKENS=12000", "worker");
  check(run.status === 0 && (await healthy(SMALL)), "a worker with a 12,000-token context budget runs in its place", run.stderr.trim().split("\n").at(-1));

  // 1. Outgrowing the budget
  chat = (await api(alan, "POST", "/v1/threads")).body.id;
  let thread;
  for (let turn = 0; turn < 3; turn++) {
    const lead = turn === 0 ? `The code word is ${CODE}. ` : `Note ${turn}. `;
    thread = await say(alan, chat, `${lead}Here is a long note to keep: ${FILLER} Reply with only: noted.`);
  }
  const summarized = psql(`select count(*) from run_events e join runs r on r.id = e.run_id where r.thread_id = '${chat}' and e.type = 'context.summarized'`);
  const answers = thread.messages.filter((m) => m.role === "assistant").map((m) => m.content);
  check(Number(summarized) >= 1 && answers.every((a) => a.trim().length < 40), "outgrowing the budget, a run records context.summarized, and no summary reaches an answer", `${summarized} summarized; answers ${answers.map((a) => JSON.stringify(a.trim().slice(0, 20))).join(", ")}`);

  // 2. Still remembered: in the summary, and in the answer
  const summary = spawnSync("docker", ["exec", "-i", SMALL, "python", "-"], { input: SUMMARY_READER(chat), encoding: "utf8" }).stdout ?? "";
  check(summary.includes("summary") && summary.includes(CODE), "the summary, read from the chat's checkpoint, keeps the code word", summary.replace(/\s+/g, " ").slice(0, 120));
  const asked = await say(alan, chat, "What was the code word I gave you at the start? Reply with it only.");
  const last = asked.messages.filter((m) => m.role === "assistant").at(-1)?.content ?? "";
  check(last.includes(CODE), "afterwards, the answer still has the code word from before the summary", last.slice(0, 40));

  // 3. In the chat
  await page.goto(`${APP}/auth/login?returnTo=/chat/${chat}`, { waitUntil: "networkidle0" });
  await page.type("#username", env.GEN9_SEED_USER_EMAIL);
  await page.type("#password", env.GEN9_SEED_USER_PASSWORD);
  await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }), page.click("#kc-login")]);
  const notes = await page.$$eval('[role="note"]', (ns) => ns.map((n) => n.textContent).filter((t) => /summarized/.test(t)));
  const questions = await page.$$eval("ol[aria-live] > li", (lis) => lis.length);
  check(notes.length >= 1 && questions === 8, "the chat says earlier messages were summarized, and still shows the whole chat", `${notes.length} note(s); ${questions} messages`);
  await page.evaluate(AXE);
  const { violations } = await page.evaluate(() => window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] } }));
  const blocking = violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  check(!blocking.length, "the chat with the note has no serious accessibility violations", blocking.map((v) => v.id).join(", "));
} catch (e) {
  check(false, "the run finished", e.stack ?? String(e));
} finally {
  if (chat) await api(alan, "DELETE", `/v1/threads/${chat}`).catch(() => {});
  if (controls) await api(alan, "PUT", "/v1/me/controls", controls).catch(() => {});
  docker("rm", "-f", SMALL);
  docker("start", WORKER);
  check(await healthy(WORKER), "the worker is back, as it was");
  await browser.close();
  rmSync(alan, { recursive: true, force: true });
}
console.log(failures ? `\n${failures} check(s) failed` : "\nall context checks passed");
process.exit(failures ? 1 : 0);
