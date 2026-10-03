// MCP Apps (milestone 2): a connector tool's View, rendered under its step, as the seeded user, with a
// test server the check starts itself (fixtures/apps_mcp.py: a board, a move only its View may play,
// and a View that reports what it could do). gen9-agent must allow it (CONNECTORS_ALLOWED_HOSTS; make
// setup does), and gen9-ui's sandbox must run (make up). In Chrome, with the web app's own CSP on:
//   1. the View renders under its step, from the connector's own origin on the sandbox, never the
//      web app's, and shows the tool's result
//   2. it can't reach the web app's window, and its request to an origin it didn't declare is blocked
//   3. its button plays a move through Gen9: with the connector's policy asking, only after Allow,
//      and not on a click the moment the ask appears. Asking on its own while the person types in
//      the composer, it doesn't take the focus or their keys; Deny refuses it
//   4. its link opens only on Open, with the address shown and its host in bold. Its message
//      replaces the person's draft only if they agree; into an empty composer it goes, marked as
//      the app's, and an Enter the moment it lands sends nothing
//   5. no serious accessibility violations; after a reload it renders again with its result
//   6. the model can't call the move, a tool only the View may use
// It removes the connector, deletes its chats and stops the test server.
import { spawn } from "node:child_process";
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
// Where connectors' Views run, as gen9-ui's MCP_APPS_SANDBOX_URL (under a domain, `https://{id}.apps.<domain>`)
const SANDBOX = process.env.MCP_APPS_SANDBOX_URL ?? `http://{id}.apps.localhost:${process.env.GEN9_UI_SANDBOX_PORT ?? "14003"}`;
const AXE = readFileSync(createRequire(import.meta.url).resolve("axe-core/axe.min.js"), "utf8");
const PORT = 17803;
const SERVER = `http://host.docker.internal:${PORT}/mcp`;
const FIGURE = `figure[aria-label="board's app"]`;
const ASKS = `[role="group"][aria-label="board's app asks"]`;

let failures = 0;
function check(ok, what, detail = "") {
  console.log(`${ok ? "ok  " : "FAIL"}  ${what}${detail ? `: ${detail}` : ""}`);
  if (!ok) failures++;
  return ok;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const gen9 = (configDir, args) =>
  new Promise((resolve) => {
    const child = spawn("uv", ["run", "-q", "gen9", ...args], { cwd: `${ROOT}gen9-cli`, env: { ...process.env, GEN9_CONFIG_DIR: configDir } });
    let out = "";
    child.stdout.on("data", (d) => (out += d));
    child.stderr.on("data", (d) => (out += d));
    child.on("exit", (code) => resolve({ code, out }));
  });
async function api(configDir, method, path, body) {
  await gen9(configDir, ["whoami"]);
  const token = JSON.parse(readFileSync(join(configDir, "credentials.json"), "utf8")).access_token;
  const response = await fetch(`${API}${path}`, { method, headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  return { status: response.status, body: response.status === 204 ? null : await response.json().catch(() => null) };
}
const played = () => fetch(`http://127.0.0.1:${PORT}/test/moves`).then((r) => r.json());
// The composer as the person's typing sets it (React's state follows the input event)
const setComposer = (page, value) =>
  page.$eval(
    "#composer",
    (c, v) => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(c, v);
      c.dispatchEvent(new Event("input", { bubbles: true }));
    },
    value,
  );
async function buttonWithText(page, scope, text) {
  const found = [];
  for (const b of await page.$$(`${scope} button`)) {
    const label = await b.evaluate((e) => e.textContent.trim());
    if (label === text) return b;
    found.push(label);
  }
  throw new Error(`no ${text} button in ${scope} (it has: ${found.join(", ") || "none"})`);
}
async function untilDone(page) {
  await page.waitForFunction(() => !document.querySelector('button[aria-label="Stop"]'), { timeout: 240_000, polling: 500 });
}
async function ask(page, text) {
  await page.goto(`${APP}/chat`, { waitUntil: "networkidle0" });
  await page.type("#composer", text);
  await page.keyboard.press("Enter");
  const where = async (e, step) => {
    const state = await page.evaluate(() => ({ path: location.pathname, composer: document.querySelector("#composer")?.value?.slice(0, 40) })).catch(() => ({}));
    return new Error(`${step}: ${e.message} (at ${state.path}, composer "${state.composer ?? ""}")`);
  };
  await page.waitForFunction(() => /\/chat\/[0-9a-f-]{36}/.test(location.pathname), { timeout: 30_000 }).catch(async (e) => {
    throw await where(e, "the new chat's address");
  });
  chats.push(page.url().match(/\/chat\/([0-9a-f-]{36})/)?.[1]);
  await page.waitForSelector('button[aria-label="Stop"]', { timeout: 30_000 }).catch(() => {});
  await untilDone(page).catch(async (e) => {
    throw await where(e, "the answer");
  });
}
// The View: the frame the sandbox proxy wrote it into, under the connector's origin
async function view(page, origin) {
  for (let i = 0; i < 60; i++) {
    const inner = page.frames().find((f) => f.parentFrame()?.url().startsWith(`${origin}/`));
    if (inner && (await inner.$("#result").catch(() => null))) return inner;
    await sleep(500);
  }
  throw new Error(`no View under ${origin} (frames: ${page.frames().map((f) => f.url().slice(0, 60)).join(" | ")})`);
}
const text = (frame, selector) => frame.$eval(selector, (e) => e.textContent.trim());
async function until(frame, selector, wanted, timeout = 30_000) {
  const end = Date.now() + timeout;
  let now = "";
  while (Date.now() < end) {
    now = await text(frame, selector).catch(() => "");
    if (wanted.test(now)) return now;
    await sleep(250);
  }
  return now;
}

// The test server, unless one already runs on the port
const running = await fetch(`http://127.0.0.1:${PORT}/test/moves`).then(() => true, () => false);
const server = running ? null : spawn("uv", ["run", "-q", "--with", "fastmcp==4.0.9", "python", "e2e/fixtures/apps_mcp.py", String(PORT)], { cwd: ROOT, stdio: "ignore", detached: true });
for (let i = 0; i < 60 && !(await fetch(`http://127.0.0.1:${PORT}/test/moves`).then(() => true, () => false)); i++) await sleep(500);

const alan = mkdtempSync(join(tmpdir(), "gen9-apps-"));
const chats = [];
let connector = null;
try {
  check(Boolean(await signInTerminal({ email: env.GEN9_SEED_USER_EMAIL, password: env.GEN9_SEED_USER_PASSWORD, configDir: alan })), "the seeded user signs in on the terminal");
  for (const c of (await api(alan, "GET", "/v1/me/connectors")).body ?? []) if (c.name === "board") await api(alan, "DELETE", `/v1/me/connectors/${c.id}`);
  // Its own calls run without asking; the View's move is asked about later, by the policy
  const added = await api(alan, "POST", "/v1/me/connectors", { name: "board", url: SERVER, policy: "never" });
  connector = added.body?.id;
  const moveTool = added.body?.tools?.find((t) => t.name === "move");
  if (!check(added.status === 201 && moveTool?.app?.visibility?.join() === "app", "the test server is connected, its move marked for the View only", `HTTP ${added.status} ${JSON.stringify(added.body?.detail ?? moveTool ?? "")}`)) {
    throw new Error(`add host.docker.internal:${PORT} to CONNECTORS_ALLOWED_HOSTS in gen9-agent/.env (make setup does), then make up STACKS=agent`);
  }
  const origin = SANDBOX.replaceAll("{id}", connector.replaceAll("-", ""));

  const browser = await launch({ headless: !process.env.HEADED, defaultViewport: { width: 1280, height: 900 } });
  try {
    const page = await browser.newPage();
    await page.goto(`${APP}/auth/login?returnTo=/chat`, { waitUntil: "networkidle0" });
    await page.type("#username", env.GEN9_SEED_USER_EMAIL);
    await page.type("#password", env.GEN9_SEED_USER_PASSWORD);
    await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }), page.click("#kc-login")]);

    // 1. The View under its step, from the connector's own origin
    await ask(page, "Use your board connector's show_board tool with size 3, then say in one line that the board is shown.");
    await page.waitForSelector(`${FIGURE} iframe`, { timeout: 60_000 });
    const src = await page.$eval(`${FIGURE} iframe`, (f) => f.src);
    const board = await view(page, origin);
    const result = await until(board, "#result", /cells: 3/);
    check(src.startsWith(`${origin}/`) && new URL(src).origin !== new URL(APP).origin && result === "cells: 3", "the View renders under its step, from the connector's own origin, with the tool's result", `${new URL(src).origin}; ${result}`);

    // 2. Sandboxed: no reach into the web app, no undeclared requests
    const isolated = await until(board, "#isolated", /yes|no/);
    const blocked = await until(board, "#blocked", /blocked/);
    check(isolated === "yes" && blocked === "blocked", "it can't reach the web app's window, and its undeclared request is blocked", `isolated ${isolated}, request ${blocked}`);

    // 3. A move through Gen9, asked about once the policy asks
    await api(alan, "PATCH", `/v1/me/connectors/${connector}`, { policy: "ask" });
    await board.click("#play");
    await page.waitForSelector(ASKS, { timeout: 30_000 });
    const asking = await page.$eval(ASKS, (g) => ({ text: g.textContent, focused: document.activeElement === g }));
    // A click the moment it appears isn't a decision (gen9-ui's SETTLE_MS, 500 ms)
    await (await buttonWithText(page, ASKS, "Allow")).click();
    await sleep(700);
    const early = { moves: (await played()).join(), open: Boolean(await page.$(ASKS)) };
    await (await buttonWithText(page, ASKS, "Allow")).click();
    const allowed = await until(board, "#played", /played|refused/);
    check(
      asking.text.includes("wants to use move") && asking.focused && early.moves === "" && early.open && allowed === "played 1" && (await played()).join() === "1",
      "its button plays a move through Gen9, after Allow when the policy asks, not on a click the moment the ask appears",
      `${asking.text.slice(0, 40)}; focused ${asking.focused}; early ${JSON.stringify(early)}; ${allowed}`,
    );
    // Asking on its own (a click the page didn't make) while the person types in the composer
    await board.$eval("#played", (e) => (e.textContent = ""));
    await page.click("#composer");
    await board.$eval("#play", (b) => b.click());
    await page.waitForSelector(ASKS, { timeout: 30_000 });
    await page.keyboard.type(" and ");
    const typing = await page.evaluate(() => ({ focus: document.activeElement?.id, composer: document.querySelector("#composer")?.value }));
    await sleep(600);
    await (await buttonWithText(page, ASKS, "Deny")).click();
    const denied = await until(board, "#played", /played|refused/);
    check(
      typing.focus === "composer" && typing.composer === " and " && denied.startsWith("refused") && (await played()).join() === "1",
      "asking on its own while the person types, it doesn't take the focus or their keys; Deny refuses it",
      `${JSON.stringify(typing)}; ${denied}`,
    );
    await setComposer(page, "");

    // 4. A link, only on Open; a message, into the composer
    await board.click("#open");
    await page.waitForSelector(ASKS, { timeout: 30_000 });
    const link = await page.$eval(ASKS, (g) => ({ text: g.textContent, bold: g.querySelector("strong")?.textContent }));
    // The tab at that address: Firefox makes a new tab at about:blank, then navigates it (P4-B1).
    // None on a click the moment the ask appears
    const tabs = async () => (await browser.pages()).length;
    const before = await tabs();
    await (await buttonWithText(page, ASKS, "Open example.com")).click();
    await sleep(700);
    const earlyTabs = (await tabs()) - before;
    const opened = browser.waitForTarget((t) => t.url() === "https://example.com/board-rules", { timeout: 10_000 }).catch(() => null);
    await (await buttonWithText(page, ASKS, "Open example.com")).click();
    const target = await opened;
    const tab = target?.url() ?? "";
    await (await target?.page().catch(() => null))?.close().catch(() => {});
    await page.bringToFront();
    const answered = await until(board, "#opened", /opened/);
    check(
      link.text.includes("https://example.com/board-rules") && link.bold === "example.com" && earlyTabs === 0 && tab === "https://example.com/board-rules" && answered === "opened",
      "its link opens only on Open, not on a click the moment the ask appears, the address in full and its host in bold",
      `${link.bold}; ${earlyTabs} tab(s) early; ${tab}`,
    );
    await setComposer(page, "my own words");
    await board.$eval("#send", (b) => b.click());
    await page.waitForSelector(ASKS, { timeout: 30_000 });
    const replacing = await page.$eval(ASKS, (g) => g.textContent);
    await (await buttonWithText(page, ASKS, "Keep mine")).click();
    const kept = await until(board, "#sent", /sent/);
    const draft = await page.$eval("#composer", (c) => c.value);
    check(
      replacing.includes("wants to replace your message with:Tell me about cell 1") && kept === "not sent: Message sending denied" && draft === "my own words",
      "its message replaces the person's draft only if they agree",
      `${replacing.slice(0, 60)}; ${kept}; ${draft}`,
    );
    await setComposer(page, "");
    await board.$eval("#sent", (e) => (e.textContent = ""));
    await board.click("#send");
    await page.waitForFunction(() => document.querySelector("#composer")?.value === "Tell me about cell 1", { polling: "raf", timeout: 30_000 });
    await page.keyboard.press("Enter"); // at once: meant for what was there before
    await sleep(1000);
    const sent = await until(board, "#sent", /sent/);
    const put = await page.evaluate(() => ({ composer: document.querySelector("#composer")?.value, from: document.querySelector("#composer-from")?.textContent, focus: document.activeElement?.id }));
    check(
      sent === "sent" && put.composer === "Tell me about cell 1" && /^From board’s app/.test(put.from ?? "") && put.focus === "composer",
      "into an empty composer its message goes, marked as the app's, and an Enter the moment it lands sends nothing",
      JSON.stringify(put),
    );

    // 5. Accessibility, and a reload
    await page.evaluate(AXE);
    const { violations } = await page.evaluate(() => window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] } }));
    const blocking = violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    check(!blocking.length, "the chat with a View has no serious accessibility violations", blocking.map((v) => v.id).join(", "));
    await page.reload({ waitUntil: "networkidle0" });
    const again = await until(await view(page, origin), "#result", /cells: 3/);
    check(again === "cells: 3", "after a reload it renders again with its result", again);

    // 6. The model can't call the View's own tool. The policy back to never: a model that reaches
    // for show_board instead would otherwise wait on Allow, and the check with it (P3-Z1)
    await api(alan, "PATCH", `/v1/me/connectors/${connector}`, { policy: "never" });
    await ask(page, "Call your board connector's move tool with cell 2. If you have no such tool, reply exactly: NO MOVE TOOL.");
    const steps = await page.$$eval("ol[aria-live] > li", (lis) => lis.at(-1)?.textContent ?? "");
    check((await played()).join() === "1" && !/board__move|move cell|played 2/i.test(steps), "the model can't call the move, a tool only the View may use", steps.slice(0, 100));

    await page.evaluate(() => document.querySelector('form[action="/auth/logout"]')?.requestSubmit()).catch(() => {});
    await sleep(1500);
  } finally {
    await browser.close();
  }
} catch (e) {
  check(false, "the apps check ran to the end", e.message);
} finally {
  if (connector) await api(alan, "DELETE", `/v1/me/connectors/${connector}`).catch(() => {});
  for (const chat of chats.filter(Boolean)) await api(alan, "DELETE", `/v1/threads/${chat}`).catch(() => {});
  await gen9(alan, ["logout"]).catch(() => {});
  rmSync(alan, { recursive: true, force: true });
  if (server) process.kill(-server.pid);
}

console.log(failures ? `\n${failures} check(s) failed` : "\nall checks passed");
process.exit(failures ? 1 : 0);
