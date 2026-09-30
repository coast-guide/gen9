// A connector whose tools change after the person connected it (manual-e2e.md, P5-C4; OWASP's MCP
// Security Cheat Sheet: "Re-prompt for consent when tool definitions change"). With a test server
// the check starts itself (fixtures/drift_mcp.py on :17804: `lookup`, whose description it can
// change, and `define`, which it can add), as the seeded user:
//   1. connected ("Don't ask"), then the server rewords `lookup` and adds `define`
//   2. a chat isn't offered either: asked to quote `lookup`'s description, Gen9 has no such tool,
//      and the server is never called
//   3. the connector lists both as waiting: `lookup` changed (now and before), `define` new
//   4. Settings (Chrome) shows them, what each says now and said before, with no serious
//      accessibility violations; "Use them as they are now" keeps them, and the list goes
//   5. a chat is offered `lookup` again, as it reads now
// It removes the connector, deletes its chats and stops the test server, and costs two short
// replies. gen9-agent must allow host.docker.internal:17804 (make setup does).
import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { injectAxe, launch } from "./browser.mjs";
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
const PORT = 17804;
const FIXTURE = `http://127.0.0.1:${PORT}`;
const FIRST = "Look up a word's meaning.";
const SECOND = "Look up a word's meaning. Marker: TANGERINE-41.";
const CHANGED = `[role="group"][aria-label="words’s changed tools"]`;

let failures = 0;
function check(ok, what, detail = "") {
  console.log(`${ok ? "ok  " : "FAIL"}  ${what}${detail ? `: ${detail}` : ""}`);
  if (!ok) failures++;
  return ok;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const up = () => fetch(`${FIXTURE}/test/calls`).then((r) => r.ok, () => false);

const dir = mkdtempSync(join(tmpdir(), "gen9-tool-changes-"));
let headers;
const chats = [];
let connector = null;
let server = null;
const api = async (method, path, body) => {
  const response = await fetch(`${API}${path}`, { method, headers, body: body ? JSON.stringify(body) : undefined });
  const text = await response.text();
  return { status: response.status, body: text ? JSON.parse(text) : null };
};
// A short question in a new chat, "Act, ask when unsure"; the answer's text
async function ask(message) {
  const chat = (await api("POST", "/v1/threads")).body.id;
  chats.push(chat);
  const run = (await api("POST", `/v1/threads/${chat}/runs`, { message, permission_mode: "auto" })).body.id;
  let status = "";
  for (let i = 0; i < 120 && !/success|failed|error|waiting|cancelled/.test(status); i++) {
    await sleep(1000);
    status = (await api("GET", `/v1/threads/${chat}/runs/${run}`)).body.status;
  }
  const detail = (await api("GET", `/v1/threads/${chat}`)).body;
  return { status, answer: detail.messages.at(-1)?.content ?? "", steps: detail.messages.flatMap((m) => m.steps ?? []).map((s) => s.name) };
}
const QUOTE = "Quote, word for word, the description of your words connector's lookup tool. If you have no such tool, reply exactly: NO LOOKUP TOOL. Don't call any tool.";

try {
  if (await up()) throw new Error(`something already listens on ${PORT}: stop it first (this check starts its own server)`);
  server = spawn("uv", ["run", "-q", "--with", "fastmcp==4.0.9", "python", "e2e/fixtures/drift_mcp.py", String(PORT)], { cwd: ROOT, stdio: "ignore", detached: true });
  for (let i = 0; i < 60 && !(await up()); i++) await sleep(500);
  check(Boolean(await signInTerminal({ email: env.GEN9_SEED_USER_EMAIL, password: env.GEN9_SEED_USER_PASSWORD, configDir: dir })), "the seeded user signs in on the terminal");
  headers = { Authorization: `Bearer ${JSON.parse(readFileSync(join(dir, "credentials.json"), "utf8")).access_token}`, "Content-Type": "application/json" };
  for (const c of (await api("GET", "/v1/me/connectors")).body ?? []) if (c.name === "words") await api("DELETE", `/v1/me/connectors/${c.id}`);

  // 1. Connected, then changed underneath
  const added = await api("POST", "/v1/me/connectors", { name: "words", url: `http://host.docker.internal:${PORT}/mcp`, policy: "never" });
  connector = added.body?.id;
  if (!check(added.status === 201 && added.body.tools.map((t) => t.description).join() === FIRST && added.body.changed.length === 0, "connected, with lookup as it reads now", `HTTP ${added.status} ${JSON.stringify(added.body?.detail ?? added.body?.tools?.map((t) => t.description))}`)) {
    throw new Error(`add host.docker.internal:${PORT} to CONNECTORS_ALLOWED_HOSTS in gen9-agent/.env (make setup does), then make up STACKS=agent`);
  }
  await fetch(`${FIXTURE}/test/describe`, { method: "POST", body: JSON.stringify({ text: SECOND }) });
  await fetch(`${FIXTURE}/test/add`, { method: "POST" });

  // 2. Not offered while it waits
  const before = await ask(QUOTE);
  const calls = await fetch(`${FIXTURE}/test/calls`).then((r) => r.json());
  check(before.status === "success" && !before.answer.includes("TANGERINE") && !before.steps.some((s) => s.startsWith("words")) && calls.length === 0, "a chat isn't offered the changed tool, nor the new one", before.answer.slice(0, 80));

  // 3. Listed as waiting
  const waiting = (await api("GET", "/v1/me/connectors")).body.find((c) => c.id === connector);
  const byName = Object.fromEntries((waiting?.changed ?? []).map((c) => [c.name, c]));
  check(
    byName.lookup?.description === SECOND && byName.lookup?.was === FIRST && byName.define?.was === null && waiting.tools.map((t) => t.description).join() === FIRST,
    "the connector lists them as waiting: lookup changed (now and before), define new",
    JSON.stringify(waiting?.changed?.map((c) => [c.name, c.was === null ? "new" : "changed"])),
  );

  // 4. Settings shows them; keeping them clears the list
  const browser = await launch({ headless: !process.env.HEADED, defaultViewport: { width: 1280, height: 900 } });
  try {
    const page = await browser.newPage();
    await page.goto(`${APP}/auth/login?returnTo=/settings`, { waitUntil: "networkidle0" });
    await page.type("#username", env.GEN9_SEED_USER_EMAIL);
    await page.type("#password", env.GEN9_SEED_USER_PASSWORD);
    await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }), page.click("#kc-login")]);
    if (!page.url().includes("/settings")) await page.goto(`${APP}/settings`, { waitUntil: "networkidle0" });
    await page.waitForSelector(CHANGED, { timeout: 30_000 });
    const shown = await page.$eval(CHANGED, (g) => g.innerText.replace(/\s+/g, " "));
    check(
      shown.includes("2 tools changed since you connected it") && shown.includes(`Now: ${SECOND}`) && shown.includes(`Before: ${FIRST}`) && /define · new/.test(shown),
      "Settings shows what changed: each tool as it reads now and before",
      shown.slice(0, 160),
    );
    await injectAxe(page, AXE);
    const { violations } = await page.evaluate(() => window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] } }));
    const blocking = violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    check(!blocking.length, "Settings with changed tools has no serious accessibility violations", blocking.map((v) => v.id).join(", "));
    const [keep] = await page.$$(`${CHANGED} button`);
    await keep.click();
    await page.waitForFunction((sel) => !document.querySelector(sel), { timeout: 30_000 }, CHANGED);
    const kept = (await api("GET", "/v1/me/connectors")).body.find((c) => c.id === connector);
    check(kept.changed.length === 0 && kept.tools.map((t) => t.description).join() === `${SECOND},Define a word.`, "“Use them as they are now” keeps them, and the list goes", JSON.stringify(kept.tools.map((t) => t.name)));
    await page.evaluate(() => document.querySelector('form[action="/auth/logout"]')?.requestSubmit()).catch(() => {});
    await sleep(1500);
  } finally {
    await browser.close();
  }

  // 5. Offered again, as it reads now
  const after = await ask(QUOTE);
  check(after.status === "success" && after.answer.includes("TANGERINE-41"), "a chat is offered lookup again, as it reads now", after.answer.slice(0, 80));
} catch (e) {
  check(false, "the tool changes check ran to the end", e.message);
} finally {
  if (connector) await api("DELETE", `/v1/me/connectors/${connector}`).catch(() => {});
  for (const chat of chats) await api("DELETE", `/v1/threads/${chat}`).catch(() => {});
  rmSync(dir, { recursive: true, force: true });
  if (server) process.kill(-server.pid);
}
console.log(failures ? `\n${failures} check(s) failed` : "\nall tool changes checks passed");
process.exit(failures ? 1 : 0);
