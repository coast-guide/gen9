// Built-in skills (gen9-agent's skills.py), through `gen9 ask` as the seeded user:
//   1. asked for a brief, the agent reads /skills/research-brief/SKILL.md and the answer has its
//      structure (Answer, Findings, Uncertain, Sources, "As of"); the chat names the step
//   2. an unrelated question reads no skill
//   3. asked to edit the skill, the edit is refused and the file in the worker is unchanged
// It reads the seeded user from gen9-keycloak/.env, the run's steps from gen9-postgres, and the
// skill's hash from the worker (`docker exec`). It costs a brief (with web searches) and two short
// replies, and deletes its chats at the end (chats.mjs).
import { spawn, spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { chatOf, deleteChats } from "./chats.mjs";
import { ROOT, signInTerminal } from "./signin.mjs";

const env = Object.fromEntries(
  readFileSync(`${ROOT}gen9-keycloak/.env`, "utf8")
    .split("\n")
    .filter((line) => /^[A-Z0-9_]+=/.test(line))
    .map((line) => [line.slice(0, line.indexOf("=")), line.slice(line.indexOf("=") + 1)]),
);
const SKILL = "/skills/research-brief/SKILL.md";

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
// The skill as the worker has it, in its agent's folder (definition.py)
const skillHash = () =>
  spawnSync(
    "docker",
    ["exec", "gen9-agent-worker-1", "sh", "-c", "sha256sum $(python -c 'from gen9_agent.definition import GEN9; print(GEN9 / \"skills\")')/research-brief/SKILL.md"],
    { encoding: "utf8" },
  ).stdout.split(" ")[0];

const gen9 = (configDir, ...args) =>
  new Promise((resolve) => {
    const child = spawn("uv", ["run", "-q", "gen9", ...args], { cwd: `${ROOT}gen9-cli`, env: { ...process.env, GEN9_CONFIG_DIR: configDir } });
    let out = "";
    child.stdout.on("data", (d) => (out += d));
    child.stderr.on("data", (d) => (out += d));
    child.on("exit", (code) => resolve({ code, out }));
  });
// The tools a chat's runs used, in order, with their file paths
const stepsOf = (thread) =>
  psql(
    `select coalesce(string_agg(e.data->>'name' || coalesce(' ' || (e.data->'args'->>'file_path'), ''), '; ' order by e.seq), '')` +
      ` from run_events e join runs r on r.id = e.run_id where r.thread_id = '${thread}' and e.type = 'tool.started'`,
  );
const chats = [];
const ask = async (configDir, question) => {
  const { out } = await gen9(configDir, "ask", question);
  const thread = chatOf(out);
  chats.push(thread);
  return { answer: out.split("\nContinue this chat")[0].trim(), steps: thread ? stepsOf(thread) : "" };
};

const configDir = mkdtempSync(join(tmpdir(), "gen9-skills-"));
try {
  const signedIn = await signInTerminal({ email: env.GEN9_SEED_USER_EMAIL, password: env.GEN9_SEED_USER_PASSWORD, configDir });
  check(Boolean(signedIn), "the seeded user signs in on the terminal", signedIn ?? "");

  // 1. A brief: the skill is read and followed
  const brief = await ask(configDir, "Give me a research brief on the current stable release of Valkey.");
  const structured = ["## Answer", "## Findings", "## Uncertain", "## Sources"].every((h) => brief.answer.includes(h)) && /As of /.test(brief.answer);
  check(brief.steps.includes(`read_file ${SKILL}`), "asked for a brief, the agent reads the research-brief skill", brief.steps.slice(0, 120));
  check(structured, "the brief has the skill's structure (Answer, Findings, Uncertain, Sources, As of)", brief.answer.split("\n")[0]);

  // 2. Not every question
  const plain = await ask(configDir, "What is 17 times 23? Reply with the number only.");
  check(!plain.steps.includes("/skills/") && plain.answer.includes("391"), "an unrelated question reads no skill", `${plain.answer} (${plain.steps || "no steps"})`);

  // 3. Read-only
  const before = skillHash();
  const edit = await ask(
    configDir,
    `Edit ${SKILL} so its Answer section allows five sentences. Reply with Done, or with why you couldn't.`,
  );
  const after = skillHash();
  check(
    before.length === 64 && before === after && !/^done\b/i.test(edit.answer),
    "asked to edit the skill, the edit is refused and the file is unchanged",
    `${edit.answer.slice(0, 100)} (${edit.steps})`,
  );
} catch (e) {
  check(false, "the skills check ran to the end", e.message);
} finally {
  const left = await deleteChats(configDir, chats).catch((e) => [e.message]);
  check(!left.length, "its chats are deleted", left.length ? `left: ${left.join(", ")}` : `${chats.length} chat(s)`);
  await gen9(configDir, "logout").catch(() => {});
  rmSync(configDir, { recursive: true, force: true });
}

console.log(failures ? `\n${failures} check(s) failed` : "\nall checks passed");
process.exit(failures ? 1 : 0);
