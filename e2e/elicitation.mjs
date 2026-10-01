// A connector's server asking the person mid-call (MCP elicitation, milestone 2), as the seeded user,
// with a test server the check starts itself: fixtures/elicit_mcp.py, whose tools ask for a form
// (plan_trip), in two rounds (book_table), for a page to be opened (connect_calendar) and for an
// address that isn't a web page (open_notes). gen9-agent must allow it (CONNECTORS_ALLOWED_HOSTS;
// make setup does). In Chrome, unless said otherwise:
//   1. the run waits with a card that names the connector and asks the server's fields in its order;
//      no serious accessibility violations; still waiting after a worker restart; filled and sent,
//      the tool's answer uses them
//   2. Decline: the tool is told, and says nothing was booked
//   3. an address: shown in full with its host in bold; Open, then Done
//   4. two rounds in one call: the second question comes as a card of its own, and the tool uses
//      both answers (M9, 4c-4)
//   5. an address that isn't a web page (javascript:): shown, no Open, declined (M9, U1)
//   6. gen9 ask answers a form in the terminal, and declines such an address unasked
// It removes the connector, deletes its chats and stops the test server.
import { execFileSync, spawn } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { bypassCSP, injectAxe, launch } from "./browser.mjs";
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
const PORT = 17802;
const SERVER = `http://host.docker.internal:${PORT}/mcp`;
const CARD = 'form[aria-label="travel asks"]';
const PLAN = "Use your travel connector's plan_trip tool, then tell me in one line what it booked.";

let failures = 0;
function check(ok, what, detail = "") {
  console.log(`${ok ? "ok  " : "FAIL"}  ${what}${detail ? `: ${detail}` : ""}`);
  if (!ok) failures++;
  return ok;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const gen9 = (configDir, args, input) =>
  new Promise((resolve) => {
    const child = spawn("uv", ["run", "-q", "gen9", ...args], { cwd: `${ROOT}gen9-cli`, env: { ...process.env, GEN9_CONFIG_DIR: configDir } });
    let out = "";
    child.stdout.on("data", (d) => (out += d));
    child.stderr.on("data", (d) => (out += d));
    child.on("exit", (code) => resolve({ code, out }));
    if (input !== undefined) child.stdin.end(input);
  });
async function api(configDir, method, path, body) {
  await gen9(configDir, ["whoami"]);
  const token = JSON.parse(readFileSync(join(configDir, "credentials.json"), "utf8")).access_token;
  const response = await fetch(`${API}${path}`, { method, headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  return { status: response.status, body: response.status === 204 ? null : await response.json().catch(() => null) };
}
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
const lastAnswer = (page) => page.$$eval("ol[aria-live] > li", (lis) => lis.at(-1)?.textContent ?? "");
// Sends `text` in a new chat and waits for the card; the chat is kept for clean-up first
async function start(page, text) {
  await page.goto(`${APP}/chat`, { waitUntil: "networkidle0" });
  await page.type("#composer", text);
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => /\/chat\/[0-9a-f-]{36}/.test(location.pathname), { timeout: 30_000 });
  chats.push(page.url().match(/\/chat\/([0-9a-f-]{36})/)?.[1]);
  await page.waitForSelector(CARD, { timeout: 180_000 });
}

// The test server, unless one already runs on the port
const running = await fetch(`http://127.0.0.1:${PORT}/mcp`, { method: "POST" }).then(() => true, () => false);
const server = running ? null : spawn("uv", ["run", "-q", "--with", "fastmcp==4.0.9", "python", "e2e/fixtures/elicit_mcp.py", String(PORT)], { cwd: ROOT, stdio: "ignore", detached: true });
for (let i = 0; i < 60 && !(await fetch(`http://127.0.0.1:${PORT}/mcp`, { method: "POST" }).then(() => true, () => false)); i++) await sleep(500);

const alan = mkdtempSync(join(tmpdir(), "gen9-elicit-"));
const chats = [];
try {
  check(Boolean(await signInTerminal({ email: env.GEN9_SEED_USER_EMAIL, password: env.GEN9_SEED_USER_PASSWORD, configDir: alan })), "the seeded user signs in on the terminal");
  for (const c of (await api(alan, "GET", "/v1/me/connectors")).body ?? []) if (c.name === "travel") await api(alan, "DELETE", `/v1/me/connectors/${c.id}`);
  const added = await api(alan, "POST", "/v1/me/connectors", { name: "travel", url: SERVER, policy: "never" });
  if (!check(added.status === 201 && added.body?.tools?.length === 4, "the test server is connected", `HTTP ${added.status} ${JSON.stringify(added.body?.detail ?? "")}`)) {
    throw new Error("add CONNECTORS_ALLOWED_HOSTS with host.docker.internal:17802 to gen9-agent/.env (make setup does), then make up STACKS=agent");
  }

  const browser = await launch({ headless: !process.env.HEADED, defaultViewport: { width: 1280, height: 900 } });
  try {
    const page = await browser.newPage();
    await bypassCSP(page); // for the injected axe script
    await page.goto(`${APP}/auth/login?returnTo=/chat`, { waitUntil: "networkidle0" });
    await page.type("#username", env.GEN9_SEED_USER_EMAIL);
    await page.type("#password", env.GEN9_SEED_USER_PASSWORD);
    await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }), page.click("#kc-login")]);

    // 1. A form: the card, a worker restart, then filled and sent
    await start(page, PLAN);
    const card = await page.$eval(CARD, (c) => ({ text: c.textContent, labels: [...c.querySelectorAll("label span.font-medium")].map((s) => s.textContent.replace(" *", "").trim()) }));
    check(card.text.startsWith("travel asks") && card.text.includes("Where to, and for how long?") && card.labels.join(",") === "City,Nights,Class", "the card names the connector and asks the server's fields in its order", `${card.labels.join(", ")}`);
    await injectAxe(page, AXE);
    const { violations } = await page.evaluate(() => window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] } }));
    const blocking = violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    check(!blocking.length, "the card has no serious accessibility violations", blocking.map((v) => v.id).join(", "));
    execFileSync("docker", ["restart", "gen9-agent-worker-1"]);
    await page.reload({ waitUntil: "load" });
    check(Boolean(await page.waitForSelector(CARD, { timeout: 60_000 }).catch(() => null)), "still waiting after a worker restart");
    await page.type(`${CARD} #field-city`, "Lisbon");
    await page.$eval(`${CARD} #field-nights`, (i) => (i.value = ""));
    await page.type(`${CARD} #field-nights`, "3");
    await page.select(`${CARD} #field-class`, "biz");
    await (await buttonWithText(page, CARD, "Send")).click();
    await page.waitForFunction((c) => !document.querySelector(c), { timeout: 60_000 }, CARD);
    await untilDone(page);
    const booked = await lastAnswer(page);
    // The class as the form sent it (`biz`) or as its option reads (Business): the tool answers
    // with the first, and the model may quote it or put it in words
    check(/Lisbon/.test(booked) && /\b3\b|three/i.test(booked) && /\bbiz\b|business/i.test(booked), "sent, the tool's answer uses the form", booked.slice(0, 120));

    // 2. Decline
    await start(page, PLAN);
    await (await buttonWithText(page, CARD, "Decline")).click();
    await page.waitForFunction((c) => !document.querySelector(c), { timeout: 60_000 }, CARD);
    await untilDone(page);
    const declined = await lastAnswer(page);
    check(/declin|nothing was booked|didn.t book|not booked|no booking/i.test(declined), "declined, the tool is told and books nothing", declined.slice(0, 120));

    // 3. An address to open
    await start(page, "Use your travel connector's connect_calendar tool, then say in one line what happened.");
    const shown = await page.$eval(CARD, (c) => ({ text: c.textContent, bold: c.querySelector("strong")?.textContent }));
    check(shown.text.includes("https://calendar.example.com/connect?session=abc") && shown.bold === "calendar.example.com", "an address is shown in full, its host in bold", shown.bold);
    // The tab at that address: Firefox makes a new tab at about:blank, then navigates it, and as
    // the host doesn't exist it ends on its error page, which names the address it tried (P4-B1)
    const address = (t) => (t.url().startsWith("about:neterror") ? new URLSearchParams(t.url().split("?")[1]).get("u") ?? "" : t.url());
    const opened = browser.waitForTarget((t) => address(t) === "https://calendar.example.com/connect?session=abc", { timeout: 10_000 }).catch(() => null);
    await (await buttonWithText(page, CARD, "Open calendar.example.com")).click();
    const target = await opened;
    const tab = target ? address(target) : "";
    // The new tab took the foreground: close it and come back to the chat
    await (await target?.page().catch(() => null))?.close().catch(() => {});
    await page.bringToFront();
    // Back in front, with Done enabled by the Open (a click on a page still in the background is lost)
    await page.waitForFunction(
      (c) => document.visibilityState === "visible" && [...document.querySelectorAll(`${c} button`)].some((b) => b.textContent.trim() === "Done" && !b.disabled),
      { timeout: 10_000 },
      CARD,
    ).catch(async (e) => {
      const state = await page.evaluate((c) => ({
        visibility: document.visibilityState,
        buttons: [...document.querySelectorAll(`${c} button`)].map((b) => `${b.textContent.trim()}${b.disabled ? " (disabled)" : ""}`).join(", "),
      }), CARD);
      const pages = (await browser.pages()).map((p) => p.url().slice(0, 50)).join(" | ");
      throw new Error(`${e.message}: ${state.visibility}; buttons ${state.buttons}; tabs ${pages}; new tab ${tab || "none"}`);
    });
    await (await buttonWithText(page, CARD, "Done")).click();
    await page.waitForFunction((c) => !document.querySelector(c), { timeout: 60_000 }, CARD).catch(async (e) => {
      const state = await page.$eval(CARD, (c) => ({
        text: c.textContent.slice(-160),
        done: [...c.querySelectorAll("button")].map((b) => `${b.textContent.trim()}${b.disabled ? " (disabled)" : ""}`).join(", "),
      })).catch(() => ({ text: "no card", done: "" }));
      throw new Error(`${e.message}: buttons ${state.done}; card ends: ${state.text}`);
    });
    await untilDone(page);
    const calendar = await lastAnswer(page);
    check(tab.startsWith("https://calendar.example.com/") && /accept|opened|connect/i.test(calendar), "Open opens it in a new tab, and the tool hears it was accepted", `${tab.slice(0, 50)}; ${calendar.slice(0, 80)}`);

    // 4. Two rounds in one tool call: each question its own card, and both answers used
    await start(page, "Use your travel connector's book_table tool, then tell me in one line what it booked.");
    await page.type(`${CARD} #field-day`, "Friday");
    await page.$eval(`${CARD} #field-people`, (i) => (i.value = ""));
    await page.type(`${CARD} #field-people`, "4");
    await (await buttonWithText(page, CARD, "Send")).click();
    const second = await page.waitForFunction((c) => [...document.querySelectorAll(c)].find((f) => /what time/.test(f.textContent)) || false, { timeout: 120_000 }, CARD).then(() => true, () => false);
    if (second) {
      await page.select(`${CARD} #field-time`, "19:00");
      await (await buttonWithText(page, CARD, "Send")).click();
      await page.waitForFunction((c) => !document.querySelector(c), { timeout: 60_000 }, CARD);
      await untilDone(page);
    }
    const table = await lastAnswer(page);
    check(second && /Friday/.test(table) && /\b4\b|four/i.test(table) && /19:00|7(:00)? ?pm/i.test(table), "two rounds in one call: the second question gets its own card, and the tool uses both answers", table.slice(0, 120));

    // 5. An address that isn't a web page: shown, never opened, declined
    await start(page, "Use your travel connector's open_notes tool, then tell me in one line what happened.");
    const notes = await page.$eval(CARD, (c) => ({ text: c.textContent, open: [...c.querySelectorAll("button")].some((b) => /^Open/.test(b.textContent.trim())) }));
    await (await buttonWithText(page, CARD, "Decline")).click();
    await page.waitForFunction((c) => !document.querySelector(c), { timeout: 60_000 }, CARD);
    await untilDone(page);
    check(notes.text.includes("javascript:alert(document.domain)") && /opens only those \(http or https\)/.test(notes.text) && !notes.open, "an address that isn't a web page is shown, has no Open, and is declined", notes.text.slice(-110));

    await page.evaluate(() => document.querySelector('form[action="/auth/logout"]')?.requestSubmit()).catch(() => {});
    await sleep(1500);
  } finally {
    await browser.close();
  }

  // 6. In the terminal
  const terminal = await gen9(alan, ["ask", PLAN], "Rome\n4\n1\ny\n");
  chats.push(terminal.out.match(/--thread ([0-9a-f-]{36})/)?.[1]);
  check(terminal.out.includes("travel asks") && /Rome/.test(terminal.out.split("Send it?")[1] ?? ""), "gen9 ask answers a form in the terminal", terminal.out.split("\n").filter(Boolean).slice(-3).join(" | ").slice(0, 160));
  const notes = await gen9(alan, ["ask", "Use your travel connector's open_notes tool, then tell me in one line what happened."], "y\n\n");
  chats.push(notes.out.match(/--thread ([0-9a-f-]{36})/)?.[1]);
  check(/Gen9 opens only web addresses/.test(notes.out) && !/Open it in your browser/.test(notes.out), "gen9 ask declines an address that isn't a web page, unasked", notes.out.split("\n").filter(Boolean).slice(-3).join(" | ").slice(0, 160));
} catch (e) {
  check(false, "the elicitation check ran to the end", e.message);
} finally {
  for (const c of (await api(alan, "GET", "/v1/me/connectors").catch(() => ({ body: [] }))).body ?? []) {
    if (c.name === "travel") await api(alan, "DELETE", `/v1/me/connectors/${c.id}`).catch(() => {});
  }
  for (const chat of chats.filter(Boolean)) await api(alan, "DELETE", `/v1/threads/${chat}`).catch(() => {});
  await gen9(alan, ["logout"]).catch(() => {});
  rmSync(alan, { recursive: true, force: true });
  if (server) process.kill(-server.pid);
}

console.log(failures ? `\n${failures} check(s) failed` : "\nall checks passed");
process.exit(failures ? 1 : 0);
