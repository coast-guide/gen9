// Gen9's agent, defined as a folder (gen9-agent's definition.py), through `gen9 ask` and Chrome as
// the seeded user:
//   1. a run records the version of the definition it ran with: the hash of the folder the worker has
//   2. asked to fact-check a claim, the agent delegates to its declared fact-checker subagent, and
//      the chat in Chrome names it ("Asked the fact checker: …")
//   3. the skills in the folder are offered to the agent (research-brief)
// It reads the seeded user from gen9-keycloak/.env, runs and steps from gen9-postgres, and the
// folder's hash from the worker (`docker exec`). It costs a fact-check (with a web search or two)
// and a short reply, and deletes its chats at the end (chats.mjs).
import { spawn, spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { launch } from "./browser.mjs";
import { chatOf, deleteChats } from "./chats.mjs";
import { ROOT, signInTerminal } from "./signin.mjs";

const env = Object.fromEntries(
  readFileSync(`${ROOT}gen9-keycloak/.env`, "utf8")
    .split("\n")
    .filter((line) => /^[A-Z0-9_]+=/.test(line))
    .map((line) => [line.slice(0, line.indexOf("=")), line.slice(line.indexOf("=") + 1)]),
);
const APP = process.env.APP_URL ?? "http://localhost:14000";

let failures = 0;
function check(ok, what, detail = "") {
  console.log(`${ok ? "ok  " : "FAIL"}  ${what}${detail ? `: ${detail}` : ""}`);
  if (!ok) failures++;
  return ok;
}

const psql = (query) =>
  spawnSync("docker", ["exec", "gen9-postgres-postgres-1", "psql", "-U", "postgres", "-d", "gen9_agent", "-tAc", query], {
    encoding: "utf8",
  }).stdout.trim();
const gen9 = (configDir, ...args) =>
  new Promise((resolve) => {
    const child = spawn("uv", ["run", "-q", "gen9", ...args], { cwd: `${ROOT}gen9-cli`, env: { ...process.env, GEN9_CONFIG_DIR: configDir } });
    let out = "";
    child.stdout.on("data", (d) => (out += d));
    child.stderr.on("data", (d) => (out += d));
    child.on("exit", (code) => resolve({ code, out }));
  });
const chats = [];
const ask = async (configDir, question) => {
  const { out } = await gen9(configDir, "ask", question);
  const thread = chatOf(out);
  chats.push(thread);
  return { answer: out.split("\nContinue this chat")[0].trim(), thread };
};

const configDir = mkdtempSync(join(tmpdir(), "gen9-agents-"));
try {
  const signedIn = await signInTerminal({ email: env.GEN9_SEED_USER_EMAIL, password: env.GEN9_SEED_USER_PASSWORD, configDir });
  check(Boolean(signedIn), "the seeded user signs in on the terminal", signedIn ?? "");

  // 1 and 2. A fact-check: the subagent, and the version on the run
  const claim = "PostgreSQL 18 was first released in September 2025.";
  const checked = await ask(configDir, `Use your fact-checker subagent to verify this claim, then reply with its verdict line only: ${claim}`);
  const deployed = spawnSync(
    "docker",
    ["exec", "gen9-agent-worker-1", "python", "-c", "from gen9_agent.definition import GEN9, version; print(version(GEN9))"],
    { encoding: "utf8" },
  ).stdout.trim();
  const recorded = psql(`select agent_version from runs where thread_id = '${checked.thread}' order by created_at desc limit 1`);
  check(/^[0-9a-f]{12}$/.test(deployed) && recorded === deployed, "the run records the version of the agent's folder", `${recorded} = ${deployed}`);
  const delegated = psql(
    `select e.data->'args'->>'subagent_type' from run_events e join runs r on r.id = e.run_id` +
      ` where r.thread_id = '${checked.thread}' and e.type = 'tool.started' and e.data->>'name' = 'task'`,
  );
  check(delegated.split("\n").includes("fact-checker"), "asked to fact-check, the agent delegates to its fact-checker", `${delegated || "no task"}; ${checked.answer.slice(0, 90)}`);

  // The chat names it, in Chrome
  const browser = await launch({ headless: !process.env.HEADED, defaultViewport: { width: 1280, height: 900 } });
  try {
    const page = await browser.newPage();
    await page.goto(`${APP}/auth/login?returnTo=/chat/${checked.thread}`, { waitUntil: "networkidle0" });
    await page.type("#username", env.GEN9_SEED_USER_EMAIL);
    await page.type("#password", env.GEN9_SEED_USER_PASSWORD);
    await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }), page.click("#kc-login")]);
    if (!page.url().includes(checked.thread)) await page.goto(`${APP}/chat/${checked.thread}`, { waitUntil: "networkidle0" });
    await page.click("ol[aria-live] > li:last-child details > summary");
    const tools = await page.$$eval('ul[aria-label="Tools used"] > li', (lis) => lis.map((li) => li.textContent.trim()));
    check(tools.some((t) => t.startsWith("Asked the fact checker:")), "the chat names the subagent", tools.find((t) => t.startsWith("Asked")) ?? tools.join(" | "));
    await page.goto(`${APP}/settings`, { waitUntil: "networkidle0" });
    await page.evaluate(() => document.querySelector('form[action="/auth/logout"]')?.requestSubmit()).catch(() => {});
    await new Promise((r) => setTimeout(r, 1500));
  } finally {
    await browser.close();
  }

  // 3. The folder's skills
  const skills = await ask(configDir, "Which skills do you have? Reply with their names only, comma-separated.");
  check(skills.answer.includes("research-brief"), "the folder's skills are offered to the agent", skills.answer);
} catch (e) {
  check(false, "the agents check ran to the end", e.message);
} finally {
  const left = await deleteChats(configDir, chats).catch((e) => [e.message]);
  check(!left.length, "its chats are deleted", left.length ? `left: ${left.join(", ")}` : `${chats.length} chat(s)`);
  await gen9(configDir, "logout").catch(() => {});
  rmSync(configDir, { recursive: true, force: true });
}

console.log(failures ? `\n${failures} check(s) failed` : "\nall checks passed");
process.exit(failures ? 1 : 0);
