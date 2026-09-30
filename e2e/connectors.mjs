// Connectors (milestone 2), as the seeded user, through Chrome and the API, with DeepWiki's public
// MCP server (https://mcp.deepwiki.com/mcp: read-only tools, no sign-in):
//   1. Settings > Connectors: adding it connects first and lists its 3 tools, "Ask every time" by
//      default; an internal address is refused with a reason; no serious accessibility violations
//   2. another person sees none of it
//   3. in a chat, the agent's call to it waits for Allow ("Gen9 wants to use deepwiki: …"), then
//      runs, and the step reads "Used deepwiki: …"
//   4. with "Don't ask", the next call runs without a card
//   5. Remove (after a confirm) disconnects it
// It removes the connector and deletes its chats at the end, and costs two short research replies.
import { spawn } from "node:child_process";
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
const DEEPWIKI = "https://mcp.deepwiki.com/mcp";
const CARD = 'section[aria-label="Gen9 needs your approval"]';
const ASK = (n) =>
  `Use your deepwiki connector's read_wiki_structure tool for the repository langchain-ai/deepagents, then reply with the names of ${n} of its documentation topics, comma-separated, nothing else.`;

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
  const response = await fetch(`${API}${path}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  return { status: response.status, body: response.status === 204 ? null : await response.json().catch(() => null) };
}
async function buttonWithText(page, scope, text) {
  for (const b of await page.$$(`${scope} button`)) if ((await b.evaluate((e) => e.textContent.trim())) === text) return b;
  throw new Error(`no ${text} button in ${scope}`);
}
async function untilDone(page) {
  await page.waitForFunction(() => !document.querySelector('button[aria-label="Stop"]'), { timeout: 240_000, polling: 500 });
}

const alan = mkdtempSync(join(tmpdir(), "gen9-connectors-"));
const ada = mkdtempSync(join(tmpdir(), "gen9-connectors-admin-"));
const chats = [];
try {
  check(Boolean(await signInTerminal({ email: env.GEN9_SEED_USER_EMAIL, password: env.GEN9_SEED_USER_PASSWORD, configDir: alan })), "the seeded user signs in on the terminal");
  check(Boolean(await signInTerminal({ email: env.GEN9_SEED_ADMIN_EMAIL, password: env.GEN9_SEED_ADMIN_PASSWORD, configDir: ada })), "another person signs in on the terminal");
  // Start clean: no connector named deepwiki from an earlier run
  for (const c of (await api(alan, "GET", "/v1/me/connectors")).body ?? []) if (c.name === "deepwiki") await api(alan, "DELETE", `/v1/me/connectors/${c.id}`);

  const browser = await launch({ headless: !process.env.HEADED, defaultViewport: { width: 1280, height: 900 } });
  try {
    const page = await browser.newPage();
    await bypassCSP(page); // for the injected axe script
    await page.goto(`${APP}/auth/login?returnTo=/settings`, { waitUntil: "networkidle0" });
    await page.type("#username", env.GEN9_SEED_USER_EMAIL);
    await page.type("#password", env.GEN9_SEED_USER_PASSWORD);
    await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }), page.click("#kc-login")]);
    if (!page.url().endsWith("/settings")) await page.goto(`${APP}/settings`, { waitUntil: "networkidle0" });

    // 1. Add it, and be refused an internal address
    const add = async (name, url) => {
      await (await buttonWithText(page, "main", "Add a connector")).click();
      const form = 'form[aria-label="Add a connector"]';
      await page.waitForSelector(form);
      await page.type(`${form} input:not([type=url]):not([type=password])`, name);
      await page.type(`${form} input[type=url]`, url);
      await (await buttonWithText(page, form, "Add")).click();
    };
    await add("inside", "https://gen9-postgres:5432/mcp");
    const refused = await page
      .waitForFunction(() => document.querySelector('form[aria-label="Add a connector"] [role=alert]')?.textContent, { timeout: 30_000 })
      .then((h) => h.jsonValue());
    check(/private network/.test(refused), "an internal address is refused, with the reason", refused);
    await (await buttonWithText(page, 'form[aria-label="Add a connector"]', "Cancel")).click();
    await add("deepwiki", DEEPWIKI);
    await page.waitForFunction(() => [...document.querySelectorAll("main p")].some((p) => p.textContent === "deepwiki"), { timeout: 60_000 });
    const row = await page.evaluate(() => {
      const select = document.querySelector('select[aria-label="When Gen9 asks before using deepwiki"]');
      const summary = select?.closest("div.grid")?.querySelector("summary")?.textContent;
      return { policy: select?.value, summary };
    });
    check(row.policy === "ask" && /mcp\.deepwiki\.com · 3 tools/.test(row.summary ?? ""), "adding it lists its tools, asking every time by default", `${row.summary}; ${row.policy}`);
    await injectAxe(page, AXE);
    const { violations } = await page.evaluate(() => window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] } }));
    const blocking = violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    check(!blocking.length, "Settings with a connector has no serious accessibility violations", blocking.map((v) => v.id).join(", "));

    // 2. Nobody else sees it
    const others = await api(ada, "GET", "/v1/me/connectors");
    check(others.status === 200 && !others.body.some((c) => c.name === "deepwiki"), "another person sees none of it");

    // 3. In a chat, its call waits for Allow
    await page.goto(`${APP}/chat`, { waitUntil: "networkidle0" });
    await page.type("#composer", ASK(3));
    await page.keyboard.press("Enter");
    await page.waitForSelector(CARD, { timeout: 180_000 });
    chats.push(page.url().match(/\/chat\/([0-9a-f-]{36})/)?.[1]);
    const card = await page.$eval(CARD, (c) => c.textContent);
    check(card.includes("Gen9 wants to use deepwiki: read wiki structure"), "the connector call waits for Allow, saying what it would use", card.slice(0, 90));
    await (await buttonWithText(page, CARD, "Allow")).click();
    await untilDone(page);
    await page.reload({ waitUntil: "networkidle0" });
    await page.click("ol[aria-live] > li:last-child details > summary").catch(() => {});
    const steps = await page.$$eval('ul[aria-label="Tools used"] > li', (lis) => lis.map((li) => li.textContent.trim()));
    check(steps.some((s) => s.startsWith("Used deepwiki: read wiki structure")), "allowed, it runs and the step names the connector", steps.join(" | "));

    // 4. "Don't ask": the next call runs at once
    await page.goto(`${APP}/settings`, { waitUntil: "networkidle0" });
    await page.select('select[aria-label="When Gen9 asks before using deepwiki"]', "never");
    await page.waitForNetworkIdle({ idleTime: 500 });
    await page.goto(`${APP}/chat`, { waitUntil: "networkidle0" });
    await page.type("#composer", ASK(2));
    await page.keyboard.press("Enter");
    await page.waitForSelector('button[aria-label="Stop"]', { timeout: 30_000 });
    // Stop shows before the address becomes the new chat's: wait for it, or the chat is left behind
    await page.waitForFunction(() => /\/chat\/[0-9a-f-]{36}/.test(location.pathname), { timeout: 30_000 });
    chats.push(page.url().match(/\/chat\/([0-9a-f-]{36})/)?.[1]);
    let asked = false;
    for (let i = 0; i < 480 && (await page.$('button[aria-label="Stop"]')); i++) {
      if (await page.$(CARD)) asked = true;
      await sleep(500);
    }
    check(!asked, "with \"Don't ask\", the call runs without a card");

    // 5. Remove it
    await page.goto(`${APP}/settings`, { waitUntil: "networkidle0" });
    const row5 = await page.$('select[aria-label="When Gen9 asks before using deepwiki"]');
    const remove = await row5.evaluateHandle((s) => [...s.parentElement.querySelectorAll("button")].find((b) => b.textContent.trim() === "Remove"));
    await remove.click();
    await page.waitForSelector('[role="alertdialog"]');
    await (await buttonWithText(page, '[role="alertdialog"]', "Remove")).click();
    await page.waitForFunction(() => !document.querySelector('select[aria-label="When Gen9 asks before using deepwiki"]'), { timeout: 30_000 });
    const left = await api(alan, "GET", "/v1/me/connectors");
    check(!left.body.some((c) => c.name === "deepwiki"), "Remove disconnects it");

    await page.evaluate(() => document.querySelector('form[action="/auth/logout"]')?.requestSubmit()).catch(() => {});
    await sleep(1500);
  } finally {
    await browser.close();
  }
} catch (e) {
  check(false, "the connectors check ran to the end", e.message);
} finally {
  for (const c of (await api(alan, "GET", "/v1/me/connectors").catch(() => ({ body: [] }))).body ?? []) {
    if (c.name === "deepwiki") await api(alan, "DELETE", `/v1/me/connectors/${c.id}`).catch(() => {});
  }
  for (const chat of chats.filter(Boolean)) await api(alan, "DELETE", `/v1/threads/${chat}`).catch(() => {});
  for (const dir of [alan, ada]) {
    await gen9(dir, ["logout"]).catch(() => {});
    rmSync(dir, { recursive: true, force: true });
  }
}

console.log(failures ? `\n${failures} check(s) failed` : "\nall checks passed");
process.exit(failures ? 1 : 0);
