// Questions mid-task (human in the loop), through Chrome, the API and `gen9 ask`, as the seeded user:
//   1. asked to plan a trip, the agent asks which city: a card with the choices and "Other", the
//      composer waiting, "Needs you" in the sidebar, the run `waiting`; no accessibility violations
//      (desktop and phone, light and dark)
//   2. the run keeps waiting across a restart of the worker, and the card is back after a reload
//   3. another person can't answer it (404)
//   4. answered in Chrome, the run goes on and the answer uses it; a second answer gets 409; after a
//      reload the step says what was asked and answered
//   5. Stop while waiting ends the run, and the next message in that chat is answered normally
//   6. `gen9 ask` asks the question in the terminal and takes the answer from stdin
// It reads the seeded users from gen9-keycloak/.env, runs from gen9-postgres, and restarts the
// gen9-agent worker once. It costs three short trip plans and one short reply.
import { spawn, spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { bypassCSP, colourScheme, injectAxe, launch } from "./browser.mjs";
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
const PROMPT =
  "Plan a one-day trip for me. Before anything else, use ask_user to ask me which city, as a multiple choice of " +
  "Paris, Rome and Tokyo. Then give me a three-line plan.";
const CARD = 'form[aria-label="Gen9 needs your answer"]';
// A reply about Rome may never say "Rome" ("Colosseum + Roman Forum …")
const ABOUT_ROME = /Rom[ea]|Roman|Colosseum|Trevi|Vatican|Pantheon|Trastevere/;
const storedAnswers = (thread) =>
  psql(`select i.response->>'answers' from run_inputs i join runs r on r.id = i.run_id where r.thread_id = '${thread}'`);

let failures = 0;
function check(ok, what, detail = "") {
  console.log(`${ok ? "ok  " : "FAIL"}  ${what}${detail ? `: ${detail}` : ""}`);
  if (!ok) failures++;
  return ok;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const psql = (query) =>
  spawnSync("docker", ["exec", "gen9-postgres-postgres-1", "psql", "-U", "postgres", "-d", "gen9_agent", "-tAc", query], {
    encoding: "utf8",
  }).stdout.trim();
const gen9 = (configDir, args, onOutput) =>
  new Promise((resolve) => {
    const child = spawn("uv", ["run", "-q", "gen9", ...args], { cwd: `${ROOT}gen9-cli`, env: { ...process.env, GEN9_CONFIG_DIR: configDir } });
    let out = "";
    const take = (d) => {
      out += d;
      onOutput?.(out, child);
    };
    child.stdout.on("data", take);
    child.stderr.on("data", take);
    child.on("exit", (code) => resolve({ code, out }));
  });
/** Calls gen9-agent as the person signed in at `configDir` (gen9 refreshes the token first). */
async function api(configDir, method, path, body) {
  await gen9(configDir, ["whoami"]);
  const token = JSON.parse(readFileSync(join(configDir, "credentials.json"), "utf8")).access_token;
  const response = await fetch(`${API}${path}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  return { status: response.status, body: await response.json().catch(() => null) };
}
async function until(what, seconds, probe) {
  for (let i = 0; i < seconds; i++) {
    const value = probe();
    if (value) return value;
    await sleep(1000);
  }
  throw new Error(`timed out waiting for ${what}`);
}
const runOf = (thread) => psql(`select id || ' ' || status from runs where thread_id = '${thread}' order by created_at desc limit 1`).split(" ");

const alan = mkdtempSync(join(tmpdir(), "gen9-questions-"));
const ada = mkdtempSync(join(tmpdir(), "gen9-questions-admin-"));
const created = []; // the chats this check makes, deleted at the end (a waiting one stops first)
try {
  check(Boolean(await signInTerminal({ email: env.GEN9_SEED_USER_EMAIL, password: env.GEN9_SEED_USER_PASSWORD, configDir: alan })), "the seeded user signs in on the terminal");
  check(Boolean(await signInTerminal({ email: env.GEN9_SEED_ADMIN_EMAIL, password: env.GEN9_SEED_ADMIN_PASSWORD, configDir: ada })), "another person signs in on the terminal");

  const browser = await launch({ headless: !process.env.HEADED, defaultViewport: { width: 1280, height: 900 } });
  try {
    const page = await browser.newPage();
    await bypassCSP(page); // for the injected axe script
    await page.goto(`${APP}/auth/login?returnTo=/chat`, { waitUntil: "networkidle0" });
    await page.type("#username", env.GEN9_SEED_USER_EMAIL);
    await page.type("#password", env.GEN9_SEED_USER_PASSWORD);
    await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }), page.click("#kc-login")]);
    if (!page.url().endsWith("/chat")) await page.goto(`${APP}/chat`, { waitUntil: "networkidle0" });

    // 1. The question
    await page.type("#composer", PROMPT);
    await page.keyboard.press("Enter");
    await page.waitForSelector(CARD, { timeout: 180_000 });
    const thread = page.url().match(/\/chat\/([0-9a-f-]{36})/)?.[1];
    created.push(thread);
    const choices = await page.$$eval(`${CARD} label`, (labels) => labels.map((l) => l.textContent.trim()));
    check(["Paris", "Rome", "Tokyo", "Other"].every((c) => choices.includes(c)), "the agent asks with the choices and Other", choices.join(", "));
    const composer = await page.$eval("#composer", (t) => ({ disabled: t.disabled, placeholder: t.placeholder }));
    check(composer.disabled && composer.placeholder === "Answer the question above to continue", "the composer waits for the answer", composer.placeholder);
    const [run, status] = runOf(thread);
    check(status === "waiting", "the run waits for the person", status);
    const needsYou = await page
      .waitForFunction((t) => document.querySelector(`a[href="/chat/${t}"]`)?.textContent.includes("Needs you"), { timeout: 10_000 }, thread)
      .then(() => true, () => false);
    check(needsYou, "the chat says Needs you in the sidebar");
    // The card on desktop and phone, light and dark (as a11y.mjs audits every screen)
    const found = [];
    for (const viewport of [
      { width: 1280, height: 900 },
      { width: 390, height: 844, isMobile: true, hasTouch: true, deviceScaleFactor: 3 },
    ]) {
      for (const scheme of ["light", "dark"]) {
        await page.setViewport(viewport);
        await colourScheme(page, scheme);
        await page.reload({ waitUntil: "load" }); // the open event stream never lets the network idle
        await page.waitForSelector(CARD, { timeout: 30_000 });
        await page.evaluate(() => Promise.all(document.getAnimations().map((a) => a.finished.catch(() => {}))));
        await injectAxe(page, AXE);
        const { violations } = await page.evaluate(() => window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] } }));
        for (const v of violations.filter((v) => v.impact === "serious" || v.impact === "critical")) found.push(`${v.id} (${viewport.width}px, ${scheme})`);
      }
    }
    await page.setViewport({ width: 1280, height: 900 });
    await colourScheme(page, "light");
    check(!found.length, "the waiting chat has no serious accessibility violations, desktop and phone, light and dark", found.join(", "));

    // 2. Across a worker restart
    spawnSync("docker", ["restart", "gen9-agent-worker-1"]);
    await until("the worker to be healthy", 90, () =>
      spawnSync("docker", ["inspect", "-f", "{{.State.Health.Status}}", "gen9-agent-worker-1"], { encoding: "utf8" }).stdout.trim() === "healthy",
    );
    // A waiting chat keeps following its run (an open event stream): the network is never idle
    await page.reload({ waitUntil: "load" });
    const back = await page.waitForSelector(CARD, { timeout: 30_000 }).then(() => true, () => false);
    check(back && runOf(thread)[1] === "waiting", "after a worker restart the run still waits, and the card is back");

    // 3. Only its person can answer
    const inputId = psql(`select id from run_inputs where run_id = '${run}'`);
    const other = await api(ada, "POST", `/v1/threads/${thread}/runs/${run}/inputs/${inputId}`, { answers: ["Tokyo"] });
    check(other.status === 404, "another person can't answer it", String(other.status));

    // 4. Answered in Chrome
    const rome = await page.$$(`${CARD} label`).then(async (labels) => {
      for (const label of labels) if ((await label.evaluate((l) => l.textContent.trim())) === "Rome") return label;
    });
    await rome.click();
    await page.click(`${CARD} button[type=submit]`);
    await page.waitForFunction((card) => !document.querySelector(card), { timeout: 30_000 }, CARD);
    const ended = await until("the run to end", 240, () => {
      const s = runOf(thread)[1];
      return ["success", "error", "cancelled", "expired"].includes(s) && s;
    });
    const answer = await page
      .waitForFunction((about) => new RegExp(about).test(document.querySelector("ol[aria-live] > li:last-child")?.textContent ?? ""), { timeout: 30_000 }, ABOUT_ROME.source)
      .then(() => true, () => false);
    const stored = storedAnswers(thread);
    check(ended === "success" && answer && stored === '["Rome"]', "answered in Chrome, the run goes on and the reply uses the answer", `${ended}, stored ${stored}`);
    const again = await api(alan, "POST", `/v1/threads/${thread}/runs/${run}/inputs/${inputId}`, { answers: ["Paris"] });
    // ...and recorded with who tried, on which run, and why (P6-C4)
    const tried = psql(`select actor || ' ' || outcome || ' ' || (detail->>'why') from audit_events where action = 'run.answer' and target = '${run}'`);
    check(again.status === 409 && /^\S+ denied already answered$/.test(tried), "a second answer is refused, and the audit record says so", `${again.status} ${again.body?.detail ?? ""}; ${tried.replace(/^\S+/, "<sub>")}`);
    await page.reload({ waitUntil: "networkidle0" });
    await page.click("ol[aria-live] > li:last-child details > summary");
    const steps = await page.$$eval('ul[aria-label="Tools used"] > li', (lis) => lis.map((li) => li.textContent.trim()));
    const asked = steps.find((s) => s.startsWith("Asked you:")) ?? "";
    check(asked.includes("You answered: Rome"), "after a reload the step says what was asked and answered", asked);

    await page.goto(`${APP}/settings`, { waitUntil: "networkidle0" });
    await page.evaluate(() => document.querySelector('form[action="/auth/logout"]')?.requestSubmit()).catch(() => {});
    await sleep(1500);
  } finally {
    await browser.close();
  }

  // 5. Stop while waiting, then the chat goes on
  const stopped = (await api(alan, "POST", "/v1/threads")).body.id;
  created.push(stopped);
  await api(alan, "POST", `/v1/threads/${stopped}/runs`, { message: PROMPT });
  const [waitingRun] = await until("the second run to wait", 180, () => {
    const r = runOf(stopped);
    return r[1] === "waiting" && r;
  });
  await api(alan, "POST", `/v1/threads/${stopped}/runs/${waitingRun}/cancel`);
  const cancelled = await until("the stop", 30, () => runOf(stopped)[1] === "cancelled" && "cancelled").catch(() => runOf(stopped)[1]);
  await api(alan, "POST", `/v1/threads/${stopped}/runs`, { message: "Never mind the trip. Reply with just: OK" });
  const next = await until("the follow-up", 120, () => {
    const [id, s] = runOf(stopped);
    return id !== waitingRun && ["success", "error"].includes(s) && s;
  });
  const reply = psql(
    `select e.data->>'text' from run_events e join runs r on r.id = e.run_id where r.thread_id = '${stopped}' and e.type = 'message.completed' order by r.created_at desc, e.seq desc limit 1`,
  );
  check(cancelled === "cancelled" && next === "success" && /OK/.test(reply), "Stop while waiting ends the run; the next message is answered", `${cancelled}, ${next}: ${reply.slice(0, 40)}`);

  // 6. In the terminal
  let answered = false;
  const asked = await gen9(alan, ["ask", PROMPT], (out, child) => {
    if (!answered && out.includes("Gen9 needs your answer:") && out.trimEnd().endsWith(">")) {
      answered = true;
      child.stdin.write("2\n"); // Rome
    }
  });
  const terminalThread = asked.out.match(/--thread ([0-9a-f-]{36})/)?.[1];
  created.push(terminalThread);
  const shown = asked.out.split("Gen9 needs your answer:")[1] ?? "";
  const stored = terminalThread ? storedAnswers(terminalThread) : "";
  check(
    asked.code === 0 && answered && /2\. Rome/.test(shown) && stored === '["Rome"]' && ABOUT_ROME.test(shown.split(">").slice(1).join(">")),
    "gen9 ask asks in the terminal and takes the answer from stdin",
    `exit ${asked.code}, stored ${stored}`,
  );
} catch (e) {
  check(false, "the questions check ran to the end", e.message);
} finally {
  for (const thread of created.filter(Boolean)) await api(alan, "DELETE", `/v1/threads/${thread}`).catch(() => {});
  for (const dir of [alan, ada]) {
    await gen9(dir, ["logout"]).catch(() => {});
    rmSync(dir, { recursive: true, force: true });
  }
}

console.log(failures ? `\n${failures} check(s) failed` : "\nall checks passed");
process.exit(failures ? 1 : 0);
