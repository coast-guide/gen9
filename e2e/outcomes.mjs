// Rubric-graded outcomes (gen9-agent/README.md, "Scheduled tasks"; docs/design/screens/scheduled.md),
// as Anthropic's Managed Agents define theirs. As the seeded user:
//   1. Scheduled > New task with "Done when" (a rubric) and its tries: the row says it's checked
//      against a rubric
//   2. Run now: the rubric asks for a closing line the message doesn't, so the first answer is
//      graded short of it; a second run in the same chat takes the grader's findings and meets
//      it. While a run is graded, the row says so; after, "meets its rubric in 2 tries"
//   3. the chat shows both verdicts under the answers they graded, criterion by criterion (axe
//      clean); one email for the whole firing, "… is done", none for the first try
//   4. with one try, a run short of its rubric ends there, "short of its rubric", and emails "…
//      didn't meet its rubric"
// It deletes its tasks and chats at the end, and costs four short replies and four gradings.
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
const MAILPIT = process.env.MAILPIT_URL ?? "http://localhost:15002";
// Mailpit's API asks for its password (user gen9; MAILPIT_UI_PASSWORD in gen9-keycloak/.env)
const MAIL = { headers: { Authorization: `Basic ${Buffer.from(`gen9:${env.MAILPIT_UI_PASSWORD ?? ""}`).toString("base64")}` } };
const AXE = readFileSync(createRequire(import.meta.url).resolve("axe-core/axe.min.js"), "utf8");
const TAG = randomBytes(3).toString("hex");
const CLOSING = "Checked by Gen9";

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
async function mails(subject) {
  const found = await (await fetch(`${MAILPIT}/api/v1/search?query=${encodeURIComponent(`to:${env.GEN9_SEED_USER_EMAIL} subject:"${subject}"`)}`, MAIL)).json();
  return found.messages ?? [];
}
const threadsOf = (taskId) => psql(`select coalesce(string_agg(id::text, ',' order by created_at), '') from threads where task_id = '${taskId}'`).split(",").filter(Boolean);
// A chat's verdicts, in the order of its tries
const verdicts = (threadId) => psql(`select coalesce(string_agg(e.result, ',' order by e.iteration), '') from outcome_evaluations e join runs r on r.id = e.run_id where r.thread_id = '${threadId}'`);
async function until(what, test, seconds, everyMs = 1000) {
  for (let i = 0; i < (seconds * 1000) / everyMs; i++) {
    const got = await test();
    if (got) return got;
    await sleep(everyMs);
  }
  throw new Error(`${what} didn't happen in ${seconds} s`);
}

const alan = mkdtempSync(join(tmpdir(), "gen9-outcomes-alan-"));
const browser = await launch({ headless: !process.env.HEADED, defaultViewport: { width: 1280, height: 900 } });
const page = await browser.newPage();
const tasks = [];
const rowText = async (name) => (await page.$$eval('ul[aria-label="Scheduled tasks"] > li', (lis, n) => lis.map((li) => li.textContent).find((t) => t.includes(n)) ?? "", name));
try {
  await page.goto(`${APP}/auth/login?returnTo=/scheduled`, { waitUntil: "networkidle0" });
  await page.type("#username", env.GEN9_SEED_USER_EMAIL);
  await page.type("#password", env.GEN9_SEED_USER_PASSWORD);
  await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }), page.click("#kc-login")]);
  check(Boolean(await signInTerminal({ email: env.GEN9_SEED_USER_EMAIL, password: env.GEN9_SEED_USER_PASSWORD, configDir: alan })), "the seeded user signs in on the terminal");
  psql(`update users set notify = 'all' where email = '${env.GEN9_SEED_USER_EMAIL}'`);

  // 1. New task, with what done looks like
  const name = `e2e graded ${TAG}`;
  await page.goto(`${APP}/scheduled`, { waitUntil: "networkidle0" });
  for (const b of await page.$$("main button")) if ((await b.evaluate((e) => e.textContent.trim())) === "New task") await b.click();
  await page.waitForSelector("#new-name");
  await page.type("#new-name", name);
  await page.type("#new-prompt", "Name three primary colours, one per line.");
  await page.select("#new-kind", "daily");
  await page.select("#new-mode", "auto");
  check(!(await page.$("#new-tries")), "Tries appears only once there is a rubric");
  await page.type("#new-rubric", `- Names three colours, one per line\n- Its last line is exactly: ${CLOSING}`);
  await page.waitForSelector("#new-tries");
  await page.click('form[aria-label="New task"] button[type="submit"]');
  await page.waitForFunction((n) => [...document.querySelectorAll('ul[aria-label="Scheduled tasks"] li')].some((li) => li.textContent.includes(n)), { timeout: 20_000 }, name);
  const graded = psql(`select id from tasks where name = '${name}'`);
  tasks.push(graded);
  check(psql(`select max_iterations || ' ' || (rubric like '%${CLOSING}%') from tasks where id = '${graded}'`) === "3 true", "the task keeps its rubric and 3 tries");
  check((await rowText(name)).includes("Checked against a rubric, 3 tries at most"), "the row says it's checked against a rubric", await rowText(name));

  // 2. Run now: short of the rubric, revised in the same chat, then met
  const ran = await api(alan, "POST", `/v1/tasks/${graded}/run`);
  check(ran.status === 202, "Run now starts it", `${ran.status}`);
  let checking = false;
  const [chat] = await until(
    "the firing to end",
    async () => {
      const last = (await api(alan, "GET", "/v1/tasks")).body.find((t) => t.id === graded)?.runs[0];
      checking ||= Boolean(last?.checking);
      if (!last || last.status !== "success" || last.checking || !last.outcome) return null;
      // Met, or not applicable, or short of it after all three tries
      const over = last.outcome !== "needs_revision" || verdicts(last.thread_id).split(",").length === 3;
      return over ? threadsOf(graded) : null;
    },
    300,
    500,
  );
  const results = verdicts(chat);
  const runs = psql(`select count(*) from runs where thread_id = '${chat}'`);
  // Short first, then revised in the same chat until met: the second try, or the third when the model's
  // revision misses too (it did once in G7); each earlier try short of the rubric
  const tries = Number(runs);
  const expected = [...Array(tries - 1).fill("needs_revision"), "satisfied"].join(",");
  check(threadsOf(graded).length === 1 && (tries === 2 || tries === 3) && results === expected, "the first answer is graded short of its rubric, and a later run in the same chat meets it", `${runs} runs; ${results}`);
  check(checking, "while a run is graded, the task says it's being checked");
  const thread = (await api(alan, "GET", `/v1/threads/${chat}`)).body;
  const asked = thread.messages.filter((m) => m.role === "user").map((m) => m.content);
  check(asked.length === tries && /try 1 of 3/.test(asked[1]) && asked[1].includes(CLOSING), "the second run's message is the grader's findings", asked[1]?.slice(0, 160));
  // Marked as the grader's, its findings as data for the agent, and shown as Gen9's, not the person's
  // (gen9-learn.md, M9, F19 and its follow-up)
  const second = thread.messages.filter((m) => m.role === "user")[1];
  await page.goto(`${APP}/chat/${chat}`, { waitUntil: "networkidle0" });
  const shown = await page.$$eval("ol[aria-live] > li", (lis) => lis.map((li) => li.innerText).find((t) => t.startsWith("From the rubric check")) ?? "");
  check(
    second?.revision === true && second.content.includes("<grader-findings>") && /try 1 of 3/.test(shown) && !shown.includes("<grader-findings>") && !shown.includes("You said"),
    "the grader's findings reach the agent marked as data, and the chat shows them as the rubric check's",
    shown.replace(/\s+/g, " ").slice(0, 160),
  );
  const answers = thread.messages.filter((m) => m.role === "assistant");
  const withVerdict = thread.messages.filter((m) => m.evaluation).map((m) => m.evaluation.result);
  check(withVerdict.join(",") === expected && answers.at(-1).content.trim().endsWith(CLOSING), "the chat carries each verdict, under the answer it graded", withVerdict.join(","));
  await page.goto(`${APP}/scheduled`, { waitUntil: "networkidle0" });
  check((await rowText(name)).includes(`Done · meets its rubric in ${tries} tries`), `the row says it met its rubric in ${tries} tries`, await rowText(name));

  // 3. The chat, and the one email
  await page.goto(`${APP}/chat/${chat}`, { waitUntil: "networkidle0" });
  const summaries = await page.$$eval("details > summary", (s) => s.map((e) => e.textContent.trim()).filter((t) => /rubric/.test(t)));
  check(
    summaries.length === tries &&
      /^Short of its rubric · \d of \d met$/.test(summaries[0]) &&
      new RegExp(`^Meets its rubric · (\\d) of \\1 \\(try ${tries}\\)$`).test(summaries.at(-1)),
    "the chat shows each verdict folded to one line",
    summaries.join(" | "),
  );
  for (const s of await page.$$("details > summary")) if (/rubric/.test(await s.evaluate((e) => e.textContent))) await s.click();
  const opened = await page.$$eval('ul[aria-label="Criteria"] li', (lis) => lis.map((li) => li.textContent));
  check(opened.some((t) => t.startsWith("Not met:")) && opened.some((t) => t.startsWith("Met:")), "opened, it lists each criterion as met or not, with why", `${opened.length} criteria`);
  await page.evaluate(AXE);
  const { violations } = await page.evaluate(() => window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] } }));
  const blocking = violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  check(!blocking.length, "the chat with its verdicts has no serious accessibility violations", blocking.map((v) => v.id).join(", "));
  const done = await until("the done email", async () => {
    const m = await mails(`${name} is done`);
    return m.length ? m : null;
  }, 30);
  await sleep(3000);
  check(done.length === 1 && (await mails(name)).length === 1, "one email for the firing, “… is done”, none for the first try", `${(await mails(name)).length} emails`);

  // 4. Out of tries
  const strict = `e2e strict ${TAG}`;
  const made = await api(alan, "POST", "/v1/tasks", {
    name: strict,
    prompt: "Name one primary colour.",
    schedule: { kind: "daily", time: "03:00" },
    time_zone: "UTC",
    permission_mode: "auto",
    rubric: `- Its last line is exactly: ${CLOSING}`,
    max_iterations: 1,
  });
  tasks.push(made.body.id);
  await api(alan, "POST", `/v1/tasks/${made.body.id}/run`);
  const unmet = await until("the didn't-meet email", async () => {
    const m = await mails(`${strict} didn't meet its rubric`);
    return m.length ? m : null;
  }, 240);
  const [strictChat] = threadsOf(made.body.id);
  check(unmet.length === 1 && verdicts(strictChat) === "needs_revision" && psql(`select count(*) from runs where thread_id = '${strictChat}'`) === "1", "with one try, a run short of its rubric ends there and emails “… didn't meet its rubric”", verdicts(strictChat));
  await page.goto(`${APP}/scheduled`, { waitUntil: "networkidle0" });
  // One try graded: no "in N tries", which counting other chats' verdicts would add
  check((await rowText(strict)).endsWith("Done · short of its rubric"), "the row says it's short of its rubric, after its one try", await rowText(strict));
} catch (e) {
  check(false, "the run finished", e.stack ?? String(e));
} finally {
  for (const id of tasks) {
    for (const chat of threadsOf(id)) await api(alan, "DELETE", `/v1/threads/${chat}`).catch(() => {});
    await api(alan, "DELETE", `/v1/tasks/${id}`).catch(() => {});
  }
  await browser.close();
  rmSync(alan, { recursive: true, force: true });
}
console.log(failures ? `\n${failures} check(s) failed` : "\nall outcome checks passed");
process.exit(failures ? 1 : 0);
