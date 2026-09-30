// Past chats as the agent's tools (gen9-agent/README.md, "Past chats"), as Claude's chat search. As
// the seeded user:
//   1. a chat mentions an unguessable boat name (memory is put back after it, so only a search can
//      find it); a new chat asked about it searches the past chats, answers with the name, and
//      lists the earlier chat as a source
//   2. Settings > Memory > "Search and reference past chats" (axe clean): turned off, a new chat
//      asked the same isn't offered the tools and doesn't know the name; turned on again
// It deletes its chats, and costs four short replies.
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
// Letters only: a hex suffix can read as a number ("Marram056e01"), and the model once answered
// "Marram" (P5-Z1)
const BOAT = `Marram${[...randomBytes(6)].map((b) => String.fromCharCode(97 + (b % 26))).join("")}`;
const QUESTION = "In an earlier chat I told you the name of the lighthouse keeper's boat in my story. Search my past chats and reply with the boat's name only.";

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
const latest = (threadId) => psql(`select coalesce((select status from runs where thread_id = '${threadId}' order by created_at desc limit 1), '')`);
async function ask(configDir, message) {
  const chat = (await api(configDir, "POST", "/v1/threads")).body.id;
  chats.push(chat);
  await api(configDir, "POST", `/v1/threads/${chat}/runs`, { message, permission_mode: "auto" });
  for (let i = 0; i < 240 && !["success", "error", "cancelled", "expired"].includes(latest(chat)); i++) await sleep(1000);
  const thread = (await api(configDir, "GET", `/v1/threads/${chat}`)).body;
  return { chat, thread, answer: thread.messages.filter((m) => m.role === "assistant").at(-1)?.content ?? "", steps: thread.messages.flatMap((m) => m.steps ?? []) };
}

const alan = mkdtempSync(join(tmpdir(), "gen9-past-chats-alan-"));
const browser = await launch({ headless: !process.env.HEADED, defaultViewport: { width: 1280, height: 900 } });
const page = await browser.newPage();
const chats = [];
let memoryBefore = null;
try {
  check(Boolean(await signInTerminal({ email: env.GEN9_SEED_USER_EMAIL, password: env.GEN9_SEED_USER_PASSWORD, configDir: alan })), "the seeded user signs in on the terminal");
  psql(`update users set search_past_chats = true where email = '${env.GEN9_SEED_USER_EMAIL}'`);

  // 1. An earlier chat, found by a search from a new one
  memoryBefore = (await api(alan, "GET", "/v1/me/memory")).body?.content ?? "";
  const earlier = await ask(alan, `I'm drafting a story. The lighthouse keeper's boat is called the ${BOAT}. Reply with only: ok`);
  await api(alan, "PUT", "/v1/me/memory", { content: memoryBefore });
  const indexed = async () => ((await api(alan, "GET", `/v1/search?q=${encodeURIComponent(BOAT)}&mode=keyword`)).body ?? []).some((h) => h.thread_id === earlier.chat);
  for (let i = 0; i < 60 && !(await indexed()); i++) await sleep(1000);
  check(!(await api(alan, "GET", "/v1/me/memory")).body.content.includes(BOAT) && (await indexed()), "the earlier chat is searchable, and memory doesn't hold the name");
  const found = await ask(alan, QUESTION);
  const search = found.steps.find((s) => s.name === "search_past_chats");
  check(found.answer.includes(BOAT) && Boolean(search), "a new chat searches the past chats and answers with the name", `${found.answer.slice(0, 40)}; ${found.steps.map((s) => s.name).join(", ")}`);
  check((search?.sources ?? []).some((s) => s.url.endsWith(`/chat/${earlier.chat}`)), "the earlier chat is listed as a source", (search?.sources ?? []).map((s) => s.title).join(" | "));

  // 2. Turned off in Settings
  await page.goto(`${APP}/auth/login?returnTo=/settings`, { waitUntil: "networkidle0" });
  await page.type("#username", env.GEN9_SEED_USER_EMAIL);
  await page.type("#password", env.GEN9_SEED_USER_PASSWORD);
  await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }), page.click("#kc-login")]);
  // The switch its label names (Settings > Memory has more than one)
  await page.waitForSelector('[role="switch"]');
  const toggle = await page.evaluateHandle(() => {
    // The switch in the row its label heads (Base UI gives the id to its hidden input)
    const label = [...document.querySelectorAll("label")].find((l) => l.textContent.trim() === "Search and reference past chats");
    return label.closest(".border-t").querySelector('[role="switch"]');
  });
  check((await toggle.evaluate((e) => e.getAttribute("aria-checked"))) === "true", "Settings shows “Search and reference past chats” on");
  await page.evaluate(AXE);
  const { violations } = await page.evaluate(() => window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] } }));
  const blocking = violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  check(!blocking.length, "Settings with the switch has no serious accessibility violations", blocking.map((v) => v.id).join(", "));
  await toggle.click();
  for (let i = 0; i < 20 && psql(`select search_past_chats from users where email = '${env.GEN9_SEED_USER_EMAIL}'`) !== "f"; i++) await sleep(500);
  check(psql(`select search_past_chats from users where email = '${env.GEN9_SEED_USER_EMAIL}'`) === "f", "the switch turns it off");
  const off = await ask(alan, QUESTION);
  check(!off.steps.some((s) => s.name === "search_past_chats" || s.name === "recent_chats") && !off.answer.includes(BOAT), "turned off, a new chat isn't offered the tools and doesn't know the name", off.answer.slice(0, 60));
} catch (e) {
  check(false, "the run finished", e.stack ?? String(e));
} finally {
  psql(`update users set search_past_chats = true where email = '${env.GEN9_SEED_USER_EMAIL}'`);
  if (memoryBefore !== null) await api(alan, "PUT", "/v1/me/memory", { content: memoryBefore }).catch(() => {});
  for (const id of chats) await api(alan, "DELETE", `/v1/threads/${id}`).catch(() => {});
  await browser.close();
  rmSync(alan, { recursive: true, force: true });
}
console.log(failures ? `\n${failures} check(s) failed` : "\nall past chat checks passed");
process.exit(failures ? 1 : 0);
