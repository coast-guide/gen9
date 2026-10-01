// Gen9 over AG-UI (gen9-agent/README.md, "AG-UI"), as an AG-UI frontend drives it: `@ag-ui/client`
// 1.0's HttpAgent posts a RunAgentInput to POST /v1/agui with a token for Gen9's API (the CLI's),
// and reads the events. As the seeded user:
//   1. a message's answer streams as one text message with an unguessable phrase, between
//      RUN_STARTED and RUN_FINISHED (success); the thread is a Gen9 chat of theirs
//   2. a second run in the same thread remembers the first
//   3. in "Ask before acting" (forwardedProps.permissionMode), a request to save to memory ends
//      the run with an interrupt
//      (approval, with the answer's schema); a run resuming it resolved with an approve goes on
//      and finishes, and the memory has it (then put back); resuming one with cancelled stops the
//      run, which ends as cancelled with nothing written
//   4. another person's thread: 404; no token: 401
// It deletes its chats at the end, and costs four short replies.
import { execFileSync, spawn } from "node:child_process";
import { randomBytes, randomUUID } from "node:crypto";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { HttpAgent } from "@ag-ui/client";
import { ROOT, signInTerminal } from "./signin.mjs";

const env = Object.fromEntries(
  readFileSync(`${ROOT}gen9-keycloak/.env`, "utf8")
    .split("\n")
    .filter((line) => /^[A-Z0-9_]+=/.test(line))
    .map((line) => [line.slice(0, line.indexOf("=")), line.slice(line.indexOf("=") + 1)]),
);
const API = process.env.GEN9_API ?? "http://localhost:17000";
const PHRASE = `ibis-${randomBytes(3).toString("hex")}`;
const BIRD = `heron-${randomBytes(3).toString("hex")}`;

let failures = 0;
function check(ok, what, detail = "") {
  console.log(`${ok ? "ok  " : "FAIL"}  ${what}${detail ? `: ${detail}` : ""}`);
  if (!ok) failures++;
  return ok;
}
const psql = (sql) => execFileSync("docker", ["exec", "gen9-postgres-postgres-1", "psql", "-U", "postgres", "-d", "gen9_agent", "-tAc", sql], { encoding: "utf8" }).trim();
const gen9 = (configDir, args) =>
  new Promise((resolve) => {
    const child = spawn("uv", ["run", "-q", "gen9", ...args], { cwd: `${ROOT}gen9-cli`, env: { ...process.env, GEN9_CONFIG_DIR: configDir } });
    child.on("exit", (code) => resolve(code));
    child.stdin.end();
  });
async function token(configDir) {
  await gen9(configDir, ["whoami"]);
  return JSON.parse(readFileSync(join(configDir, "credentials.json"), "utf8")).access_token;
}
// One AG-UI run: every event the server sent, in order
async function run(configDir, threadId, input) {
  const agent = new HttpAgent({ url: `${API}/v1/agui`, headers: { Authorization: `Bearer ${await token(configDir)}` } });
  const events = [];
  await new Promise((resolve, reject) =>
    agent.run({ threadId, runId: randomUUID(), messages: [], tools: [], context: [], state: {}, forwardedProps: {}, ...input }).subscribe({ next: (e) => events.push(e), error: reject, complete: resolve }),
  );
  return events;
}
const say = (text) => ({ messages: [{ id: randomUUID(), role: "user", content: text }] });
const textOf = (events) => events.filter((e) => e.type === "TEXT_MESSAGE_CONTENT").map((e) => e.delta).join("");

const alan = mkdtempSync(join(tmpdir(), "gen9-agui-alan-"));
const ada = mkdtempSync(join(tmpdir(), "gen9-agui-ada-"));
const threads = [];
let memoryBefore = null;
try {
  check(Boolean(await signInTerminal({ email: env.GEN9_SEED_USER_EMAIL, password: env.GEN9_SEED_USER_PASSWORD, configDir: alan })), "the seeded user signs in on the terminal");
  check(Boolean(await signInTerminal({ email: env.GEN9_SEED_ADMIN_EMAIL, password: env.GEN9_SEED_ADMIN_PASSWORD, configDir: ada })), "the seeded admin signs in on the terminal");

  // 1. A message, streamed
  const thread = randomUUID();
  threads.push(thread);
  const first = await run(alan, thread, say(`Reply with exactly this and nothing else: ${PHRASE}`));
  const kinds = first.map((e) => e.type);
  const finished = first.at(-1);
  check(kinds[0] === "RUN_STARTED" && kinds.includes("TEXT_MESSAGE_START") && kinds.includes("TEXT_MESSAGE_END") && finished.type === "RUN_FINISHED" && finished.outcome?.type === "success", "a message's answer streams between RUN_STARTED and RUN_FINISHED (success)", [...new Set(kinds)].join(", "));
  check(textOf(first).includes(PHRASE), "its text has the answer", textOf(first).slice(0, 60));
  check(psql(`select u.email from threads t join users u on u.id = t.user_id where t.id = '${thread}'`) === env.GEN9_SEED_USER_EMAIL, "the thread is a Gen9 chat of the person's");

  // 2. The same thread goes on
  const second = await run(alan, thread, say("What exactly did I ask you to reply with? Reply with that word only."));
  check(textOf(second).includes(PHRASE), "a second run in the same thread remembers the first", textOf(second).slice(0, 60));

  // 3. An interrupt, and resuming it
  const asking = randomUUID();
  threads.push(asking);
  memoryBefore = (await (await fetch(`${API}/v1/me/memory`, { headers: { Authorization: `Bearer ${await token(alan)}` } })).json()).content ?? "";
  const paused = await run(alan, asking, { ...say(`Please remember that my favourite bird is the ${BIRD}. Reply with exactly: Noted.`), forwardedProps: { permissionMode: "ask" } });
  const stop = paused.at(-1);
  const [asked] = stop.outcome?.interrupts ?? [];
  check(stop.type === "RUN_FINISHED" && stop.outcome?.type === "interrupt" && asked?.reason === "approval" && asked.responseSchema?.required?.[0] === "decisions", "a memory write in Ask before acting ends the run with an approval interrupt, with the answer's schema", JSON.stringify(asked ?? {}).slice(0, 120));
  // As a client does: each approval it's shown gets an answer, until the run ends (a turn may
  // write memory more than once: plan, Surprises)
  let resumed = paused;
  const answered = [];
  for (let round = 0; round < 3 && resumed.at(-1).outcome?.type === "interrupt"; round++) {
    const open = resumed.at(-1).outcome.interrupts;
    answered.push(...open.map((i) => i.id));
    resumed = await run(alan, asking, { resume: open.map((i) => ({ interruptId: i.id, status: "resolved", payload: { decisions: [{ type: "approve" }] } })) });
  }
  const memory = (await (await fetch(`${API}/v1/me/memory`, { headers: { Authorization: `Bearer ${await token(alan)}` } })).json()).content ?? "";
  check(resumed.at(-1).type === "RUN_FINISHED" && resumed.at(-1).outcome?.type === "success" && memory.includes(BIRD) && new Set(answered).size === answered.length, "resuming it with an approve lets the run go on and finish; the memory has it", `${answered.length} approval(s), each a new interrupt; ${resumed.at(-1).outcome?.type}; memory ${memory === memoryBefore ? "unchanged" : `now ends: ${memory.trim().split("\n").at(-1)?.slice(0, 80)}`}`);

  // 3b. Resuming with cancelled stops the run, and says so (it once answered with the interrupt it
  // had just cancelled: the run still read as waiting until its workflow recorded the stop)
  const stopping = randomUUID();
  threads.push(stopping);
  const pausedAgain = await run(alan, stopping, { ...say(`Please remember that my favourite tree is the ${BIRD}. Reply with exactly: Noted.`), forwardedProps: { permissionMode: "ask" } });
  const waits = pausedAgain.at(-1).outcome?.interrupts ?? [];
  const cancelled = await run(alan, stopping, { resume: waits.map((i) => ({ interruptId: i.id, status: "cancelled" })) });
  const memoryAfter = (await (await fetch(`${API}/v1/me/memory`, { headers: { Authorization: `Bearer ${await token(alan)}` } })).json()).content ?? "";
  check(waits.length > 0 && cancelled.at(-1).type === "RUN_FINISHED" && cancelled.at(-1).outcome?.type === "cancelled" && !memoryAfter.includes(`tree is the ${BIRD}`), "resuming it with cancelled stops the run, ends as cancelled, and nothing is written", `${waits.length} interrupt(s); ${cancelled.map((e) => e.type).join(", ")}; ${cancelled.at(-1).outcome?.type}`);

  // 4. Refusals
  let theirs = null;
  try {
    await run(ada, thread, say("hello"));
  } catch (e) {
    theirs = e;
  }
  const bare = await fetch(`${API}/v1/agui`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  check(/404/.test(String(theirs?.message ?? theirs)) && bare.status === 401, "another person's thread gets 404, and no token 401", `${String(theirs?.message ?? theirs).slice(0, 60)}; ${bare.status}`);
} catch (e) {
  check(false, "the run finished", e.stack ?? String(e));
} finally {
  const apiToken = await token(alan).catch(() => null);
  if (memoryBefore !== null) await fetch(`${API}/v1/me/memory`, { method: "PUT", headers: { Authorization: `Bearer ${apiToken}`, "Content-Type": "application/json" }, body: JSON.stringify({ content: memoryBefore }) }).catch(() => {});
  for (const id of threads) await fetch(`${API}/v1/threads/${id}`, { method: "DELETE", headers: { Authorization: `Bearer ${apiToken}` } }).catch(() => {});
  for (const dir of [alan, ada]) rmSync(dir, { recursive: true, force: true });
}
console.log(failures ? `\n${failures} check(s) failed` : "\nall AG-UI checks passed");
process.exit(failures ? 1 : 0);
