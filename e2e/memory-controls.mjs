// Memory controls (gen9-agent/README.md, "Memory"), as Claude's memory can be paused and keeps
// sensitive details out. As the seeded user, with their memory put back at the end:
//   1. Settings > Memory > "Remember things about me" (axe clean): turned off, a new chat doesn't
//      know what memory holds, and asked to remember something saves nothing and says memory is off
//   2. turned on again, a new chat knows it
//   3. a sensitive detail mentioned in passing isn't saved; one the person asks to remember is
// It costs five short replies.
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
const TAG = randomBytes(3).toString("hex");
// What memory holds: an unguessable code, which a model repeats as given (a word with a suffix
// glued on, it shortened to the word)
const FRUIT = `FRUIT-${TAG}`;
const TREE = `rowan-${TAG}`;
const CONDITION = `vertigo-${TAG}`;
const BLOOD = `O-negative-${TAG}`;

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
async function ask(message) {
  const chat = (await api(alan, "POST", "/v1/threads")).body.id;
  chats.push(chat);
  await api(alan, "POST", `/v1/threads/${chat}/runs`, { message, permission_mode: "auto" });
  for (let i = 0; i < 240 && !["success", "error", "cancelled", "expired"].includes(latest(chat)); i++) await sleep(1000);
  const thread = (await api(alan, "GET", `/v1/threads/${chat}`)).body;
  return thread.messages.filter((m) => m.role === "assistant").at(-1)?.content ?? "";
}
const memory = async () => (await api(alan, "GET", "/v1/me/memory")).body?.content ?? "";
const remembering = () => psql(`select remember from users where email = '${env.GEN9_SEED_USER_EMAIL}'`);
async function flip() {
  await page.goto(`${APP}/settings`, { waitUntil: "networkidle0" });
  const toggle = await page.evaluateHandle(() => {
    // The switch in the row its label heads (Base UI gives the id to its hidden input)
    const label = [...document.querySelectorAll("label")].find((l) => l.textContent.trim() === "Remember things about me");
    return label.closest(".border-t").querySelector('[role="switch"]');
  });
  const was = remembering();
  await toggle.click();
  for (let i = 0; i < 20 && remembering() === was; i++) await sleep(500);
  return toggle;
}

const alan = mkdtempSync(join(tmpdir(), "gen9-memory-controls-alan-"));
const browser = await launch({ headless: !process.env.HEADED, defaultViewport: { width: 1280, height: 900 } });
const page = await browser.newPage();
const chats = [];
let before = null;
try {
  check(Boolean(await signInTerminal({ email: env.GEN9_SEED_USER_EMAIL, password: env.GEN9_SEED_USER_PASSWORD, configDir: alan })), "the seeded user signs in on the terminal");
  psql(`update users set remember = true where email = '${env.GEN9_SEED_USER_EMAIL}'`);
  before = await memory();
  await api(alan, "PUT", "/v1/me/memory", { content: `- Their fruit club code is ${FRUIT}.` });

  // 1. Off
  await page.goto(`${APP}/auth/login?returnTo=/settings`, { waitUntil: "networkidle0" });
  await page.type("#username", env.GEN9_SEED_USER_EMAIL);
  await page.type("#password", env.GEN9_SEED_USER_PASSWORD);
  await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }), page.click("#kc-login")]);
  await page.evaluate(AXE);
  const { violations } = await page.evaluate(() => window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] } }));
  const blocking = violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  check(!blocking.length, "Settings with both memory switches has no serious accessibility violations", blocking.map((v) => v.id).join(", "));
  await flip();
  check(remembering() === "f", "“Remember things about me” turns memory off");
  const unknown = await ask("What's my fruit club code? If you don't know, reply with only: unknown");
  check(!unknown.includes(FRUIT), "off, a new chat doesn't know the code memory holds", unknown.slice(0, 60));
  const refused = await ask(`Please remember that my favourite tree is the ${TREE}.`);
  const held = await memory();
  check(!held.includes(TREE) && held.includes(FRUIT) && /off|settings/i.test(refused), "asked to remember, it saves nothing and says memory is off", refused.slice(0, 80));

  // 2. On again
  await flip();
  check(remembering() === "t", "turned on again");
  const known = await ask("What's my fruit club code? Reply with it only.");
  check(known.includes(FRUIT), "on, a new chat knows the code", known.slice(0, 60));

  // 3. Sensitive details
  await ask(`By the way, I was diagnosed with ${CONDITION} last month. Anyway, what is 12 times 12? Reply with the number only.`);
  check(!(await memory()).includes(CONDITION), "a health detail mentioned in passing isn't saved");
  const saved = await ask(`Please remember this about me: my blood type is ${BLOOD}.`);
  const kept = await memory();
  check(kept.includes(BLOOD), "one the person asks to remember is saved",
    `${kept.split("\n").find((l) => /blood/i.test(l))?.slice(0, 80) ?? "no line about it"}; said "${saved.slice(0, 80)}"`);
} catch (e) {
  check(false, "the run finished", e.stack ?? String(e));
} finally {
  psql(`update users set remember = true where email = '${env.GEN9_SEED_USER_EMAIL}'`);
  if (before !== null) await api(alan, "PUT", "/v1/me/memory", { content: before }).catch(() => {});
  for (const id of chats) await api(alan, "DELETE", `/v1/threads/${id}`).catch(() => {});
  await browser.close();
  rmSync(alan, { recursive: true, force: true });
}
console.log(failures ? `\n${failures} check(s) failed` : "\nall memory control checks passed");
process.exit(failures ? 1 : 0);
