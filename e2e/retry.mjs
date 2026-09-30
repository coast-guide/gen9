// Retry from the checkpoint (human in the loop), as the seeded user, through Chrome, the API and
// `gen9 ask`, with gen9-models' router stopped so that every attempt of a turn fails:
//   1. the run waits for Retry instead of ending: a card "Gen9 couldn't finish" with the reason in
//      plain words, the composer waiting, a request of kind `retry`; no serious accessibility
//      violations
//   2. with the router back, Retry continues the same run: it finishes, the question is in the
//      chat once, and it is still the one run
//   3. `gen9 ask` offers "Retry? [Y/n]": Enter retries once the router is back; "n" stops the run
// It stops and starts the gen9-models router (and always starts it again), deletes the chats it
// made, and costs a few one-word replies.
import { spawn, spawnSync } from "node:child_process";
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
const ROUTER = "gen9-models-litellm-1";
const CARD = `section[aria-label="Gen9 couldn't finish"]`;
const ASK = "Reply with exactly: OK";

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
const health = (name) => spawnSync("docker", ["inspect", "-f", "{{.State.Health.Status}}", name], { encoding: "utf8" }).stdout.trim();
async function until(what, seconds, probe) {
  for (let i = 0; i < seconds; i++) {
    const value = probe();
    if (value) return value;
    await sleep(1000);
  }
  throw new Error(`timed out waiting for ${what}`);
}
const stopRouter = () => spawnSync("docker", ["stop", ROUTER]);
async function startRouter() {
  spawnSync("docker", ["start", ROUTER]);
  await until("the router to be healthy", 120, () => health(ROUTER) === "healthy");
}
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
const runsOf = (thread) => psql(`select string_agg(id || ' ' || status, ',' order by created_at) from runs where thread_id = '${thread}'`);

const alan = mkdtempSync(join(tmpdir(), "gen9-retry-"));
const created = [];
try {
  check(Boolean(await signInTerminal({ email: env.GEN9_SEED_USER_EMAIL, password: env.GEN9_SEED_USER_PASSWORD, configDir: alan })), "the seeded user signs in on the terminal");

  const browser = await launch({ headless: !process.env.HEADED, defaultViewport: { width: 1280, height: 900 } });
  try {
    const page = await browser.newPage();
    await bypassCSP(page); // for the injected axe script
    await page.goto(`${APP}/auth/login?returnTo=/chat`, { waitUntil: "networkidle0" });
    await page.type("#username", env.GEN9_SEED_USER_EMAIL);
    await page.type("#password", env.GEN9_SEED_USER_PASSWORD);
    await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }), page.click("#kc-login")]);
    if (!page.url().endsWith("/chat")) await page.goto(`${APP}/chat`, { waitUntil: "networkidle0" });

    // 1. Every attempt fails: the run waits for Retry
    stopRouter();
    await page.type("#composer", ASK);
    await page.keyboard.press("Enter");
    await page.waitForSelector(CARD, { timeout: 120_000 });
    const thread = page.url().match(/\/chat\/([0-9a-f-]{36})/)?.[1];
    created.push(thread);
    const card = await page.$eval(CARD, (c) => c.textContent);
    const placeholder = await page.$eval("#composer", (t) => t.placeholder);
    const [run, status] = runsOf(thread).split(" ");
    check(
      card.includes("Gen9 couldn’t finish") && card.includes("The model provider didn't answer.") && status === "waiting",
      "every attempt failed, and the run waits for Retry with the reason in plain words",
      `${status}; ${card.slice(0, 90)}`,
    );
    check(
      placeholder === "Retry above, or stop this answer" && psql(`select kind from run_inputs where run_id = '${run}'`) === "retry",
      "the composer waits, and the request is a retry",
      placeholder,
    );
    await injectAxe(page, AXE);
    const { violations } = await page.evaluate(() => window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] } }));
    const blocking = violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    check(!blocking.length, "the Retry card has no serious accessibility violations", blocking.map((v) => v.id).join(", "));

    // 2. The router is back: Retry continues the same run
    await startRouter();
    for (const b of await page.$$(`${CARD} button`)) if ((await b.evaluate((e) => e.textContent.trim())) === "Retry") await b.click();
    await page.waitForFunction((card) => !document.querySelector(card), { timeout: 30_000 }, CARD).catch(async (e) => {
      throw new Error(`${e.message}; the card says: ${await page.$eval(CARD, (c) => c.textContent).catch(() => "?")}`);
    });
    const ended = await until("the run to end", 180, () => {
      const s = runsOf(thread).split(" ")[1];
      return ["success", "error", "cancelled", "expired"].includes(s) && s;
    });
    const detail = await api(alan, "GET", `/v1/threads/${thread}`);
    const questions = detail.body.messages.filter((m) => m.role === "user").map((m) => m.content);
    const answer = detail.body.messages.at(-1)?.content ?? "";
    check(
      ended === "success" && runsOf(thread).split(",").length === 1 && questions.length === 1 && questions[0] === ASK && /OK/.test(answer),
      "Retry continues the same run: it finishes, one run, the question once",
      `${ended}; ${runsOf(thread).split(",").length} run(s); questions ${JSON.stringify(questions)}; answer ${answer.slice(0, 30)}`,
    );
    const events = psql(`select string_agg(type, ',' order by seq) from run_events where run_id = '${run}'`);
    check(/input\.requested,input\.provided/.test(events) && events.endsWith("run.completed"), "its log shows the wait and the Retry", events);

    await page.goto(`${APP}/settings`, { waitUntil: "networkidle0" });
    await page.evaluate(() => document.querySelector('form[action="/auth/logout"]')?.requestSubmit()).catch(() => {});
    await sleep(1500);
  } finally {
    await browser.close();
  }

  // 3. In the terminal: Enter retries, "n" stops
  stopRouter();
  let offered = false;
  const retried = await gen9(alan, ["ask", ASK], (out, child) => {
    if (!offered && out.trimEnd().endsWith("Retry? [Y/n]")) {
      offered = true;
      startRouter().then(() => child.stdin.write("\n"));
    }
  });
  created.push(retried.out.match(/--thread ([0-9a-f-]{36})/)?.[1]);
  check(
    retried.code === 0 && offered && retried.out.includes("Gen9 couldn't finish: The model provider didn't answer.") && /OK/.test(retried.out.split("Retry? [Y/n]")[1] ?? ""),
    "gen9 ask offers Retry, and Enter continues once the router is back",
    `exit ${retried.code}`,
  );
  stopRouter();
  let declined = false;
  const stopped = await gen9(alan, ["ask", ASK], (out, child) => {
    if (!declined && out.trimEnd().endsWith("Retry? [Y/n]")) {
      declined = true;
      child.stdin.write("n\n");
    }
  });
  // Stopped, `gen9 ask` prints no thread to continue: the newest run is this one
  const stoppedThread = psql("select thread_id from runs order by created_at desc limit 1");
  created.push(stoppedThread);
  const stoppedStatus = runsOf(stoppedThread).split(" ")[1];
  check(declined && stopped.code === 1 && stoppedStatus === "cancelled", "answering n stops the run", `exit ${stopped.code}, ${stoppedStatus}`);
} catch (e) {
  check(false, "the retry check ran to the end", e.message);
} finally {
  if (health(ROUTER) !== "healthy") await startRouter().catch(() => {});
  for (const thread of created.filter(Boolean)) await api(alan, "DELETE", `/v1/threads/${thread}`).catch(() => {});
  await gen9(alan, ["logout"]).catch(() => {});
  rmSync(alan, { recursive: true, force: true });
}

console.log(failures ? `\n${failures} check(s) failed` : "\nall checks passed");
process.exit(failures ? 1 : 0);
