// WebKit, Safari's engine, through Playwright's build of it: Puppeteer drives only Chrome and
// Firefox, and Safari itself takes automation only with "Allow Remote Automation", a setting of its
// person's Mac (manual-e2e.md, P4-B2). As the seeded user, with this check's own test server:
//   1. sign in (Keycloak), ask, the answer streams in and stays after a reload
//   2. a connector's View renders from its own origin, can't reach the web app, and its request
//      to an origin it didn't declare is blocked
//   3. from another site, a fetch with the session's cookie is refused
//   4. the CSP blocks an injected image, and its report is logged
//   5. no serious accessibility violations (axe) on the chat with a View
//   6. a file attached in the composer is read in the chat's environment; a chat's file downloads
// Needs Playwright's WebKit: npx playwright install webkit. It removes the connector, deletes its
// chats and stops the test server.
import { spawn } from "node:child_process";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { webkit } from "playwright";
import { ROOT, signInTerminal } from "./signin.mjs";

const env = Object.fromEntries(
  readFileSync(`${ROOT}gen9-keycloak/.env`, "utf8")
    .split("\n")
    .filter((line) => /^[A-Z0-9_]+=/.test(line))
    .map((line) => [line.slice(0, line.indexOf("=")), line.slice(line.indexOf("=") + 1)]),
);
const APP = process.env.APP_URL ?? "http://localhost:14000";
const API = process.env.GEN9_API ?? "http://localhost:17000";
const SANDBOX_PORT = process.env.GEN9_UI_SANDBOX_PORT ?? "14003";
const AXE = readFileSync(createRequire(import.meta.url).resolve("axe-core/axe.min.js"), "utf8");
const PORT = 17803; // apps.mjs's test server
const SERVER = `http://host.docker.internal:${PORT}/mcp`;
const OTHER_PORT = Number(process.env.OTHER_SITE_PORT ?? 17996);
const FIGURE = `figure[aria-label="board's app"]`;

let failures = 0;
function check(ok, what, detail = "") {
  console.log(`${ok ? "ok  " : "FAIL"}  ${what}${detail ? `: ${detail}` : ""}`);
  if (!ok) failures++;
  return ok;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function api(configDir, method, path, body, raw) {
  const token = JSON.parse(readFileSync(join(configDir, "credentials.json"), "utf8")).access_token;
  const response = await fetch(`${API}${path}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, "Content-Type": raw === undefined ? "application/json" : "application/octet-stream" },
    body: raw ?? (body ? JSON.stringify(body) : undefined),
  });
  return { status: response.status, body: response.status === 204 ? null : await response.json().catch(() => null) };
}
async function until(frame, selector, wanted, timeout = 30_000) {
  const end = Date.now() + timeout;
  let now = "";
  while (Date.now() < end) {
    now = (await frame.textContent(selector).catch(() => "")) ?? "";
    if (wanted.test(now.trim())) return now.trim();
    await sleep(250);
  }
  return now.trim();
}

// The test server, unless one already runs on the port
const running = await fetch(`http://127.0.0.1:${PORT}/test/moves`).then(() => true, () => false);
const server = running ? null : spawn("uv", ["run", "-q", "--with", "fastmcp==4.0.9", "python", "e2e/fixtures/apps_mcp.py", String(PORT)], { cwd: ROOT, stdio: "ignore", detached: true });
for (let i = 0; i < 60 && !(await fetch(`http://127.0.0.1:${PORT}/test/moves`).then(() => true, () => false)); i++) await sleep(500);
// Another site, for step 3
const other = createServer((_, res) => res.writeHead(200, { "Content-Type": "text/html" }).end("<!doctype html><title>other</title>"));
await new Promise((resolve) => other.listen(OTHER_PORT, "127.0.0.1", resolve));

const alan = mkdtempSync(join(tmpdir(), "gen9-webkit-"));
const chats = [];
let connector = null;
const browser = await webkit.launch({ headless: !process.env.HEADED });
let step = "sign-in";
try {
  check(Boolean(await signInTerminal({ email: env.GEN9_SEED_USER_EMAIL, password: env.GEN9_SEED_USER_PASSWORD, configDir: alan })), "the seeded user signs in on the terminal");
  for (const c of (await api(alan, "GET", "/v1/me/connectors")).body ?? []) if (c.name === "board") await api(alan, "DELETE", `/v1/me/connectors/${c.id}`);
  connector = (await api(alan, "POST", "/v1/me/connectors", { name: "board", url: SERVER, policy: "never" })).body?.id;
  const origin = `http://${connector.replaceAll("-", "")}.apps.localhost:${SANDBOX_PORT}`;

  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  console.log("      browser:", `WebKit ${browser.version()}`);

  // 1. Sign in, ask, the answer streams in and stays after a reload
  await page.goto(`${APP}/auth/login?returnTo=/chat`, { waitUntil: "networkidle" });
  await page.fill("#username", env.GEN9_SEED_USER_EMAIL);
  await page.fill("#password", env.GEN9_SEED_USER_PASSWORD);
  await Promise.all([page.waitForURL(`${APP}/chat`, { timeout: 60_000 }), page.click("#kc-login")]);
  const last = () => page.evaluate(() => document.querySelector("ol[aria-live] > li:last-child")?.textContent ?? "");
  // Long enough to be seen streaming: a short one can finish between two looks (P3's 431e33d)
  step = "1, the streamed answer";
  await page.fill("#composer", "Write the numbers from 1 to 60, one per line, nothing else.");
  await page.keyboard.press("Enter");
  await page.waitForURL(/\/chat\/[0-9a-f-]{36}$/, { timeout: 30_000 });
  chats.push(page.url().split("/").pop());
  const lengths = new Set();
  for (const end = Date.now() + 120_000; Date.now() < end; ) {
    const text = await last();
    lengths.add(text.length);
    if (!(await page.$('button[aria-label="Stop"]')) && /60/.test(text)) break;
    await sleep(150);
  }
  await page.reload({ waitUntil: "load" });
  await page.waitForSelector("ol[aria-live] > li");
  const after = (await last()).replace(/\s+/g, " ");
  check(/Gen9 said:.*60/.test(after) && lengths.size > 2, "signed in, an answer streams in and stays after a reload", `${lengths.size} lengths seen while it streamed; ${after.slice(0, 60)}`);

  // 2. The View
  step = "2, the View";
  await page.goto(`${APP}/chat`, { waitUntil: "networkidle" });
  await page.fill("#composer", "Use your board connector's show_board tool with size 3, then say in one line that the board is shown.");
  await page.keyboard.press("Enter");
  await page.waitForURL(/\/chat\/[0-9a-f-]{36}$/, { timeout: 30_000 });
  chats.push(page.url().split("/").pop());
  // The frame can be in the page a moment before its address is (WebKit)
  await page.waitForFunction((sel) => document.querySelector(sel)?.src, `${FIGURE} iframe`, { timeout: 90_000 });
  const src = await page.$eval(`${FIGURE} iframe`, (f) => f.src);
  let board = null;
  for (let i = 0; i < 60 && !board; i++) {
    board = page.frames().find((f) => f.parentFrame()?.url().startsWith(`${origin}/`)) ?? null;
    if (board && !(await board.$("#result").catch(() => null))) board = null;
    if (!board) await sleep(500);
  }
  const result = board ? await until(board, "#result", /cells: 3/) : "(no View)";
  const isolated = board ? await until(board, "#isolated", /yes|no/) : "";
  const blocked = board ? await until(board, "#blocked", /blocked/) : "";
  check(src.startsWith(`${origin}/`) && result === "cells: 3", "the View renders under its step, from the connector's own origin, with the tool's result", `${new URL(src).origin}; ${result}`);
  check(isolated === "yes" && blocked === "blocked", "it can't reach the web app's window, and its undeclared request is blocked", `isolated ${isolated}, request ${blocked}`);

  // 5. Accessibility, on the chat with a View
  step = "5, accessibility";
  await page.evaluate(AXE);
  const { violations } = await page.evaluate(() => window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] } }));
  const serious = violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  check(!serious.length, "the chat with a View has no serious accessibility violations", serious.map((v) => v.id).join(", "));

  // 3. Another site can't read the chats with the session's cookie
  step = "3, another site";
  const own = await page.evaluate(async () => (await fetch("/api/threads")).status);
  await page.goto(`http://127.0.0.1:${OTHER_PORT}/`);
  const tries = await page.evaluate(async (app) => {
    try {
      const r = await fetch(`${app}/api/threads`, { credentials: "include" });
      return `read ${r.status}`;
    } catch (e) {
      return `blocked (${e.name})`;
    }
  }, APP);
  check(own === 200 && tries.startsWith("blocked"), "signed in the app reads the chats; from another site the same fetch is blocked", `${own}; ${tries}`);

  // 4. The CSP blocks an injected image, and its report is logged
  step = "4, the CSP";
  const since = new Date().toISOString();
  await page.goto(`${APP}/chat`, { waitUntil: "networkidle" });
  const tag = `webkit-${Date.now()}`;
  await page.evaluate((t) => { const img = new Image(); img.src = `https://httpbin.org/image/png?${t}`; document.body.append(img); }, tag);
  let logged = "";
  for (let i = 0; i < 20 && !logged.includes("[csp] img-src"); i++) {
    await sleep(500);
    logged = execFileSync("sh", ["-c", `docker logs --since ${since} gen9-ui-prod-1 2>&1`], { encoding: "utf8" });
  }
  const line = logged.split("\n").find((l) => l.includes("[csp] img-src")) ?? "";
  check(line.includes("blocked https://httpbin.org/image/png on /chat") && !logged.includes(tag), "an injected image is blocked, reported, and logged without its query", line.replace(/^.*\[csp\]/, "[csp]") || "no report logged");

  // 6. A file attached in the composer, read in the chat's environment; a file downloaded
  step = "6, files";
  const firstLine = `lighthouse-${Date.now().toString(36)}`;
  const note = join(alan, "attached-note.txt");
  writeFileSync(note, `${firstLine}\nsecond line\n`);
  await page.goto(`${APP}/chat`, { waitUntil: "networkidle" });
  const [chooser] = await Promise.all([page.waitForEvent("filechooser"), page.click('button[aria-label="Attach files"]')]);
  await chooser.setFiles(note);
  await page.waitForFunction(() => document.querySelector('ul[aria-label="Attached files"] li') && !document.querySelector('[aria-label="Attaching"]'), null, { timeout: 30_000 });
  await page.fill("#composer", "What is the first line of the file I attached? Reply with that line exactly.");
  await page.keyboard.press("Enter");
  await page.waitForURL(/\/chat\/[0-9a-f-]{36}$/, { timeout: 30_000 });
  const attachedChat = page.url().split("/").pop();
  chats.push(attachedChat);
  await page.waitForFunction(() => !document.querySelector('button[aria-label="Stop"]'), null, { timeout: 180_000 });
  const read = (await last()).replace(/\s+/g, " ");
  check(read.includes(firstLine), "a file attached in the composer is read in the chat's environment", read.slice(0, 80));
  const body = `downloaded in WebKit ${Date.now()}\n`;
  const file = (await api(alan, "POST", `/v1/threads/${attachedChat}/files?name=notes.txt`, undefined, body)).body;
  const [download] = await Promise.all([
    page.waitForEvent("download", { timeout: 15_000 }),
    page.evaluate((url) => { const a = document.createElement("a"); a.href = url; document.body.append(a); a.click(); }, `/api/threads/${attachedChat}/files/${file?.id}`),
  ]);
  const got = readFileSync(await download.path(), "utf8");
  check(download.suggestedFilename() === "notes.txt" && got === body, "a chat's file downloads as it was written", `${download.suggestedFilename()}, ${got === body ? "same bytes" : "different bytes"}`);
  await context.close();
} catch (e) {
  check(false, "the WebKit check ran to the end", `step ${step}: ${e.message.split("\n")[0]}`);
} finally {
  await browser.close();
  other.close();
  if (connector) await api(alan, "DELETE", `/v1/me/connectors/${connector}`).catch(() => {});
  for (const chat of chats.filter(Boolean)) await api(alan, "DELETE", `/v1/threads/${chat}`).catch(() => {});
  rmSync(alan, { recursive: true, force: true });
  if (server) process.kill(-server.pid);
}
console.log(failures ? `\n${failures} check(s) failed` : "\nall checks passed");
process.exit(failures ? 1 : 0);
