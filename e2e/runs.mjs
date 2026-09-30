// Answers outlive the page, in real Chrome against the running stacks:
//   1. reload in the middle of an answer    the page follows the same run again and it completes;
//                                            what assistive technology is told, while and after
//   2. leave the chat mid-answer, come back  the answer finished without anyone watching
//   3. press Stop mid-answer                 the run is cancelled on the server, not just unwatched
//   4. a task with steps                     its plan and tools show while it works, and after a reload
//   5. where that answer came from           a Sources button under it, when done and after a reload,
//                                            listing the pages without tracking parameters
// gen9-ui queues each message as a run in gen9-agent; a worker executes it (gen9-agent/README.md, "Runs").
// Needs every stack up (make up) and a provider key in gen9-models/.env. Signs in as the seeded
// user (not the admin) and deletes the chat it creates.
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { FIREFOX, launch } from "./browser.mjs";

const ROOT = new URL("..", import.meta.url).pathname;
const env = Object.fromEntries(
  (existsSync(`${ROOT}gen9-keycloak/.env`) ? readFileSync(`${ROOT}gen9-keycloak/.env`, "utf8") : "")
    .split("\n")
    .filter((line) => /^[A-Z0-9_]+=/.test(line))
    .map((line) => [line.slice(0, line.indexOf("=")), line.slice(line.indexOf("=") + 1)]),
);
const APP = process.env.APP_URL ?? "http://localhost:14000";
const LONG = (topic) => `Write about 250 words on ${topic}. Plain paragraphs, no headings.`;

let failures = 0;
function check(ok, what, detail = "") {
  console.log(`${ok ? "ok  " : "FAIL"}  ${what}${detail ? `: ${detail}` : ""}`);
  if (!ok) failures++;
  return ok;
}
// The run's status in gen9-agent's database (runs of the chat, newest first)
const runStatuses = (threadId) =>
  execFileSync("docker", [
    "exec", "gen9-postgres-postgres-1", "psql", "-U", "postgres", "-d", "gen9_agent", "-tAc",
    `select string_agg(status, ',' order by created_at desc) from runs where thread_id = '${threadId}'`,
  ]).toString().trim();

const browser = await launch({
  headless: !process.env.HEADED,
  defaultViewport: { width: 1280, height: 900 },
});
const page = await browser.newPage();
const navigation = (action) => Promise.all([page.waitForNavigation({ waitUntil: "networkidle0", timeout: 20_000 }), action]);
// Each message as shown, without the hidden "You said:" / "Gen9 said:" that names its speaker
const items = () => page.$$eval("ol[aria-live] > li", (lis) => lis.map((li) => li.textContent.trim().replace(/^(You|Gen9) said: /, "")));
const answering = () => page.$('button[aria-label="Stop"]').then(Boolean);
const lastAnswer = async () => (await items()).at(-1) ?? "";
async function ask(text) {
  await page.type("#composer", text);
  await page.click('button[aria-label="Send"]');
  // Some of the answer is on the page
  await page.waitForFunction(
    () => (document.querySelector("ol[aria-live] > li:last-child .prose, ol[aria-live] > li:last-child p")?.textContent ?? "").length > 40,
    { timeout: 120_000, polling: 250 },
  );
}
// What Chrome gives assistive technology (its accessibility tree, over CDP; manual-e2e.md, P3-D12):
// the conversation's live region and whether it's busy, the hidden status's words, and how the
// last message is named. A real screen reader can't be automated on this Mac without changing
// system settings (VoiceOver's AppleScript control and privacy grants)
let cdp;
async function told() {
  // Firefox has no DevTools protocol: the page's own ARIA, which it hands its accessibility API
  if (FIREFOX) {
    return page.evaluate(() => {
      const log = document.querySelector("ol[aria-live]");
      return {
        live: log?.getAttribute("aria-live"),
        busy: log?.getAttribute("aria-busy") === "true",
        status: document.querySelector('p[role="status"].sr-only')?.textContent.trim() ?? "",
        last: document.querySelector("ol[aria-live] > li:last-child")?.textContent.trim().slice(0, 12) ?? "",
      };
    });
  }
  cdp ??= await page.createCDPSession();
  const { root } = await cdp.send("DOM.getDocument", { depth: 0 });
  const { nodeId } = await cdp.send("DOM.querySelector", { nodeId: root.nodeId, selector: "ol[aria-live]" });
  const { nodes } = await cdp.send("Accessibility.getPartialAXTree", { nodeId, fetchRelatives: false });
  const props = Object.fromEntries((nodes.find((n) => !n.ignored)?.properties ?? []).map((p) => [p.name, p.value.value]));
  const dom = await page.evaluate(() => ({
    status: document.querySelector('p[role="status"].sr-only')?.textContent.trim() ?? "",
    last: document.querySelector("ol[aria-live] > li:last-child")?.textContent.trim().slice(0, 12) ?? "",
  }));
  // Chrome reports busy as 1 while it's set, and leaves it out otherwise
  return { live: props.live, busy: Boolean(props.busy), ...dom };
}
async function untilDone(timeout = 180_000) {
  await page.waitForFunction(() => !document.querySelector('button[aria-label="Stop"]'), { timeout, polling: 500 });
}

let threadId;
try {
  await page.goto(`${APP}/auth/login`, { waitUntil: "networkidle0" });
  await page.type("#username", env.GEN9_SEED_USER_EMAIL);
  await page.type("#password", env.GEN9_SEED_USER_PASSWORD);
  await navigation(page.click("#kc-login"));
  if (!check(page.url().startsWith(`${APP}/chat`), "signed in as the seeded user", page.url())) throw new Error("cannot continue");

  // 1. Reload mid-answer
  await page.goto(`${APP}/chat`, { waitUntil: "networkidle0" });
  const first = LONG("the history of lighthouses");
  await ask(first);
  threadId = page.url().split("/").pop();
  const before = (await lastAnswer()).length;
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForSelector("ol[aria-live] > li");
  const whileAnswering = await answering();
  const toldWhile = await told();
  await untilDone();
  const toldAfter = await told();
  const afterReload = await items();
  check(
    toldWhile.live === "polite" && toldWhile.busy && /…$/.test(toldWhile.status) && !toldAfter.busy && toldAfter.status === "Gen9 answered." && toldAfter.last.startsWith("Gen9 said:"),
    `a screen reader is told once: the conversation busy while it streams, then “Gen9 answered.” and the answer as “Gen9 said: …”${FIREFOX ? " (the page's ARIA; Firefox has no DevTools protocol)" : ""}`,
    `while: ${toldWhile.live}, busy ${toldWhile.busy}, “${toldWhile.status}”; after: busy ${toldAfter.busy}, “${toldAfter.status}”, “${toldAfter.last}…”`,
  );
  check(
    whileAnswering && afterReload.filter((t) => t === first).length === 1 && afterReload.at(-1).length > before,
    "reload mid-answer: the page follows the same run and it completes",
    `still answering after reload: ${whileAnswering}, question shown ${afterReload.filter((t) => t === first).length}×, answer ${before} → ${afterReload.at(-1).length} chars`,
  );

  // 2. Leave mid-answer, come back later
  await ask(LONG("how tides work"));
  const seen = (await lastAnswer()).length;
  await page.goto(`${APP}/settings`, { waitUntil: "networkidle0" });
  let statuses = runStatuses(threadId);
  for (let i = 0; i < 90 && statuses.startsWith("running"); i++) {
    await new Promise((resolve) => setTimeout(resolve, 2000));
    statuses = runStatuses(threadId);
  }
  await page.goto(`${APP}/chat/${threadId}`, { waitUntil: "networkidle0" });
  const back = await items();
  check(
    statuses.startsWith("success") && !(await answering()) && back.at(-1).length > seen,
    "leave mid-answer: the run finished with nobody watching, and the answer is there on return",
    `runs: ${statuses}, answer ${seen} → ${back.at(-1).length} chars`,
  );

  // 3. Stop mid-answer. A cancel reaches the worker at its next heartbeat, a second or two later: a
  // 250-word answer from today's fast model can finish in between, so this one is longer
  await ask("Write about 800 words on the migration of arctic terns. Plain paragraphs, no headings.");
  const stoppedAt = Date.now();
  await page.click('button[aria-label="Stop"]');
  await untilDone(20_000);
  const seconds = ((Date.now() - stoppedAt) / 1000).toFixed(1);
  check(
    runStatuses(threadId).startsWith("cancelled"),
    "Stop: the run is cancelled on the server and the page stops answering",
    `runs: ${runStatuses(threadId)}, ${seconds} s`,
  );

  // 4. A task with steps: the plan and the tools, live and after a reload
  const task = "Plan first with a short todo list, then do it: search the web for the current stable PostgreSQL version and say it in one line.";
  await page.type("#composer", task);
  await page.click('button[aria-label="Send"]');
  const live = await page
    .waitForFunction(
      () => {
        const plan = document.querySelectorAll('ol[aria-label="Plan"] > li').length;
        const tools = [...document.querySelectorAll('ul[aria-label="Tools used"] > li')].map((li) => li.textContent.trim());
        return plan > 0 && tools.some((t) => t.startsWith("Searched the web")) ? { plan, tools } : false;
      },
      { timeout: 120_000, polling: 250 },
    )
    .then((h) => h.jsonValue());
  if (process.env.SHOTS) await page.screenshot({ path: `${process.env.SHOTS}/activity-live.png` });
  await untilDone();
  const summary = async () => page.$$eval("ol[aria-live] > li:last-child details > summary", (s) => s.map((x) => x.textContent.trim()));
  const after = await summary();
  const SOURCES = 'ol[aria-live] > li:last-child button[aria-label^="Sources"]';
  const sourcesWhenDone = await page.$eval(SOURCES, (b) => b.getAttribute("aria-label")).catch(() => null);
  await page.reload({ waitUntil: "networkidle0" });
  const reloaded = await summary();
  if (process.env.SHOTS) {
    await page.click("ol[aria-live] > li:last-child details > summary");
    await page.screenshot({ path: `${process.env.SHOTS}/activity-history.png` });
  }
  check(
    live.plan > 0 && /^Used \d+ tools? and a plan$/.test(after[0] ?? "") && reloaded[0] === after[0],
    "a task with steps: its plan and tools show while it works, fold when done, and are there after a reload",
    `live: ${live.plan} plan items, ${live.tools.length} tools ("${live.tools[0]}"); done: "${after[0]}"; reloaded: "${reloaded[0]}"`,
  );

  // 5. Where the answer came from, whatever the model wrote (docs/design/screens/chat.md, "Sources")
  await page.click(SOURCES);
  await page.waitForSelector('[role="dialog"] a[target="_blank"]', { timeout: 10_000 }).catch(async (e) => {
    // Say what was there instead: the button's label and the dialog's text, if it opened
    const button = await page.$eval(SOURCES, (b) => b.getAttribute("aria-label")).catch(() => "no Sources button");
    const dialog = await page.$eval('[role="dialog"]', (d) => d.textContent.slice(0, 300)).catch(() => "no dialog");
    throw new Error(`${e.message} (before the reload: ${sourcesWhenDone}; now: ${button}; dialog: ${dialog})`);
  });
  const listed = await page.$eval('[role="dialog"]', (d) => ({
    heading: d.querySelector("h2")?.textContent,
    sections: [...d.querySelectorAll("h3")].map((h) => h.textContent),
    links: [...d.querySelectorAll('a[target="_blank"]')].map((a) => a.href),
  }));
  if (process.env.SHOTS) await page.screenshot({ path: `${process.env.SHOTS}/sources.png` });
  check(
    Boolean(sourcesWhenDone) && listed.heading === "Sources" && listed.links.length > 0 && !listed.links.some((u) => /[?&](utm_|trk=)/.test(u)),
    "the answer shows its Sources when done and after a reload: pages, without tracking parameters",
    `${sourcesWhenDone}; ${listed.sections.join(" / ")}; ${listed.links.length} links, e.g. ${listed.links[0]}`,
  );
  await page.keyboard.press("Escape");
} catch (error) {
  check(false, "unexpected error", error.message.split("\n")[0]);
} finally {
  if (threadId) {
    // Delete the chat the way a user does
    await page.goto(`${APP}/chat/${threadId}`, { waitUntil: "networkidle0" }).catch(() => {});
    await page.click('button[aria-label="Chat options"]').catch(() => {});
    // By its name: the menu has other items (Rename comes first)
    await page.waitForSelector("[role=menuitem]", { timeout: 5000 }).catch(() => null);
    let item = null;
    for (const el of await page.$$("[role=menuitem]")) if ((await el.evaluate((e) => e.textContent.trim())) === "Delete chat") item = el;
    if (item) {
      await item.click();
      const confirm = await page.waitForSelector('[role="alertdialog"] button:last-of-type', { timeout: 5000 }).catch(() => null);
      if (confirm) await navigation(confirm.click()).catch(() => {});
    }
    check(runStatuses(threadId) === "", "the chat and its runs are deleted", threadId);
  }
  await browser.close();
}
console.log(failures ? `\n${failures} check(s) failed` : "\nall checks passed");
process.exit(failures ? 1 : 0);
