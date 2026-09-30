// The connector directory (milestone 2): Gen9's own copy of the MCP Registry, kept by an hourly
// Schedule, as the Registry asks of the apps that read it (gen9-agent/README.md, "Connectors"). As the
// seeded user, in Chrome:
//   1. the copy is filled (the Schedule, triggered now if it hasn't run), and a second pass fetches
//      only what changed since (updated_since): most rows keep their time of sync
//   2. Settings > Connectors > Browse the directory: "github" finds GitHub's own server first, and
//      "cloudflare" Cloudflare's (com.cloudflare.mcp/mcp, public, read-only, no sign-in); no serious
//      accessibility violations
//   3. Add fills in the form (name, URL) and says it isn't reviewed by Gen9; adding it lists its tools
// It removes the connector at the end. The first pass over the Registry can take several minutes.
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { bypassCSP, injectAxe, launch } from "./browser.mjs";
import { ROOT } from "./signin.mjs";

const env = Object.fromEntries(
  readFileSync(`${ROOT}gen9-keycloak/.env`, "utf8")
    .split("\n")
    .filter((line) => /^[A-Z0-9_]+=/.test(line))
    .map((line) => [line.slice(0, line.indexOf("=")), line.slice(line.indexOf("=") + 1)]),
);
const APP = process.env.APP_URL ?? "http://localhost:14000";
const AXE = readFileSync(createRequire(import.meta.url).resolve("axe-core/axe.min.js"), "utf8");
const SERVER = "com.cloudflare.mcp/mcp";
const GITHUB = "io.github.github/github-mcp-server";
const HOST = "docs.mcp.cloudflare.com";
const PANEL = 'section[aria-label="Connector directory"]';

let failures = 0;
function check(ok, what, detail = "") {
  console.log(`${ok ? "ok  " : "FAIL"}  ${what}${detail ? `: ${detail}` : ""}`);
  if (!ok) failures++;
  return ok;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const psql = (sql) => execFileSync("docker", ["exec", "gen9-postgres-postgres-1", "psql", "-U", "postgres", "-d", "gen9_agent", "-tAc", sql], { encoding: "utf8" }).trim();
// "Run now" for the Schedule, from inside the worker, which holds Temporal's connection settings
const trigger = () =>
  execFileSync("docker", ["exec", "gen9-agent-worker-1", "python", "-c", `
import asyncio, httpx
from gen9_agent.settings import Settings
from gen9_agent.temporal import connect
from gen9_agent.keycloak_admin import KeycloakAdmin
async def main():
    s = Settings()
    async with httpx.AsyncClient() as http:
        kc = KeycloakAdmin(s, http) if s.keycloak_admin_client_secret else None
        await (await connect(s, "e2e-directory", kc)).get_schedule_handle("sync-directory").trigger()
asyncio.run(main())`]);
const syncedUntil = () => psql("select coalesce(max(synced_until)::text, '') from registry_sync");
async function untilSynced(after) {
  for (let i = 0; i < 180; i++) {
    const until = syncedUntil();
    if (until && until !== after) return until;
    await sleep(10_000);
  }
  throw new Error("the directory's sync didn't finish in 30 minutes");
}
async function buttonWithText(page, scope, text) {
  for (const b of await page.$$(`${scope} button`)) if ((await b.evaluate((e) => e.textContent.trim())) === text) return b;
  throw new Error(`no ${text} button in ${scope}`);
}

try {
  // 1. The copy: each page is saved as it comes, so the directory serves while a pass runs. The
  // first pass over the Registry can take long (it answers a page in 20 s to 2 min at times)
  // Both servers the searches below look for: on a new install the first pass is still under way,
  // and a search finds only what it has reached (GitHub's came a good while after Cloudflare's)
  const present = () => psql(`select count(*) from registry_servers where name in ('${SERVER}', '${GITHUB}')`) === "2";
  if (!present()) {
    trigger();
    for (let i = 0; i < 180 && !present(); i++) await sleep(10_000);
  }
  const total = Number(psql("select count(*) from registry_servers"));
  check(present() && total > 100, "the directory's copy holds the Registry's remote servers, Cloudflare's docs server and GitHub's among them", `${total} servers`);
  // A second pass fetches only what changed since the first (updated_since): once one has finished
  const first = syncedUntil();
  if (first) {
    const before = psql("select now()");
    trigger();
    await untilSynced(first);
    const touched = Number(psql(`select count(*) from registry_servers where synced_at >= '${before}'`));
    check(touched < total / 10, "a second pass fetches only what changed since the first", `${touched} of ${total} touched`);
  } else {
    console.log(`skip  a second pass: the first pass over the Registry hasn't finished yet (${total} servers so far)`);
  }

  const browser = await launch({ headless: !process.env.HEADED, defaultViewport: { width: 1280, height: 900 } });
  try {
    const page = await browser.newPage();
    await bypassCSP(page); // for the injected axe script
    await page.goto(`${APP}/auth/login?returnTo=/settings`, { waitUntil: "networkidle0" });
    await page.type("#username", env.GEN9_SEED_USER_EMAIL);
    await page.type("#password", env.GEN9_SEED_USER_PASSWORD);
    await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }), page.click("#kc-login")]);
    if (!page.url().includes("/settings")) await page.goto(`${APP}/settings`, { waitUntil: "networkidle0" });

    // 2. Search the directory
    await (await buttonWithText(page, "main", "Browse the directory")).click();
    await page.waitForSelector(PANEL);
    // "github" is in half the names (the Registry's io.github.<user>/ namespace, for publishers who
    // sign in with GitHub): GitHub's own server was 123rd (gen9-learn.md, M9, F11)
    const RESULTS = `${PANEL} ul[aria-label="Directory results"] > li`;
    await page.type("#directory-search", "github");
    await (await buttonWithText(page, PANEL, "Search")).click();
    await page.waitForSelector(RESULTS, { timeout: 30_000 });
    const github = await page.$$eval(RESULTS, (lis) => lis.map((li) => li.textContent));
    check(github[0]?.includes(GITHUB), "\"github\" finds GitHub's own server first, not those published under GitHub sign-ins", `first: ${(github[0] ?? "").slice(0, 80)}`);
    await page.$eval("#directory-search", (field) => field.select());
    await page.keyboard.press("Backspace");
    await page.type("#directory-search", "cloudflare");
    await (await buttonWithText(page, PANEL, "Search")).click();
    // The list is github's until cloudflare's comes
    await page.waitForFunction((sel, before) => document.querySelector(sel) && document.querySelector(sel).textContent !== before, { timeout: 30_000 }, RESULTS, github[0]).catch(() => {});
    const results = await page.$$eval(`${PANEL} ul[aria-label="Directory results"] > li`, (lis) => lis.map((li) => li.textContent));
    const found = results.find((text) => text.includes(SERVER));
    check(results[0]?.includes(SERVER) && found.includes(HOST), "\"cloudflare\" finds Cloudflare's own server first, with its host", `${results.length} results, first: ${(results[0] ?? "").slice(0, 80)}`);
    await injectAxe(page, AXE);
    const { violations } = await page.evaluate(() => window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] } }));
    const blocking = violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    check(!blocking.length, "the directory has no serious accessibility violations", blocking.map((v) => v.id).join(", "));

    // 3. Add it in one step
    const add = await page.evaluateHandle((server, panel) => [...document.querySelectorAll(`${panel} li`)].find((li) => li.textContent.includes(server))?.querySelector("button") ?? null, SERVER, PANEL);
    if (!(await add.evaluate((b) => b !== null))) throw new Error(`${SERVER} isn't among the results to add`);
    await add.click();
    const form = 'form[aria-label="Add a connector"]';
    await page.waitForSelector(form);
    const filled = await page.$eval(form, (f) => ({
      text: f.textContent,
      name: f.querySelector("input:not([type=url]):not([type=password])")?.value,
      url: f.querySelector("input[type=url]")?.value,
    }));
    check(filled.url === `https://${HOST}/mcp` && filled.name === "cloudflare" && filled.text.includes("Not reviewed by Gen9"), "Add fills in the form (name cloudflare, its URL) and says it isn't reviewed by Gen9", `${filled.name} ${filled.url}`);
    psql(`delete from connectors where name = '${filled.name}' and user_id = (select id from users where email = '${env.GEN9_SEED_USER_EMAIL}')`);
    await (await buttonWithText(page, form, "Add")).click();
    await page.waitForFunction((name) => [...document.querySelectorAll("main p")].some((p) => p.textContent === name), { timeout: 60_000 }, filled.name);
    const row = await page.evaluate((name) => document.querySelector(`select[aria-label="When Gen9 asks before using ${name}"]`)?.closest("div.grid")?.textContent ?? "", filled.name);
    check(/\d+ tools?/.test(row) && row.includes(HOST), "adding it lists its tools", row.slice(0, 90));
    // Remove it the way a person does
    const select = await page.$(`select[aria-label="When Gen9 asks before using ${filled.name}"]`);
    const remove = await select.evaluateHandle((s) => [...s.parentElement.querySelectorAll("button")].find((b) => b.textContent.trim() === "Remove"));
    await remove.click();
    await page.waitForSelector('[role="alertdialog"]');
    await (await buttonWithText(page, '[role="alertdialog"]', "Remove")).click();
    await page.waitForFunction((name) => !document.querySelector(`select[aria-label="When Gen9 asks before using ${name}"]`), { timeout: 30_000 }, filled.name);

    await page.evaluate(() => document.querySelector('form[action="/auth/logout"]')?.requestSubmit()).catch(() => {});
    await sleep(1500);
  } finally {
    await browser.close();
  }
} catch (e) {
  check(false, "the directory check ran to the end", e.message);
}

console.log(failures ? `\n${failures} check(s) failed` : "\nall checks passed");
process.exit(failures ? 1 : 0);
