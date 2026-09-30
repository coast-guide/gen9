// gen9-postgres not taking a request, as a full disk or an outage makes it (gen9-agent's
// db_unavailable.py; manual-e2e.md, P6-B3 probed it by hand), as the seeded user on a terminal:
//   1. writes refused, reads kept: with the services' role made read-only (as a full disk makes a
//      database), reading the chats answers 200, and a new chat 503 "Gen9 can't save changes right
//      now. Try again later." with Retry-After; once writable again, a new chat is made
//   2. the database down: gen9-postgres stopped, reading the chats answers 503 "Gen9's database
//      didn't answer. Try again in a moment." with Retry-After; started again, the API answers
//      again by itself, and the worker too: a chat made and deleted, its deletion run by the worker
// It makes the role read-only and stops gen9-postgres for a few seconds, and always undoes both;
// it makes no model call.
import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { ROOT, signInTerminal } from "./signin.mjs";

const env = Object.fromEntries(
  readFileSync(`${ROOT}gen9-keycloak/.env`, "utf8")
    .split("\n")
    .filter((line) => /^[A-Z0-9_]+=/.test(line))
    .map((line) => [line.slice(0, line.indexOf("=")), line.slice(line.indexOf("=") + 1)]),
);
const API = process.env.GEN9_API ?? "http://localhost:17000";
const POSTGRES = "gen9-postgres-postgres-1";
const ROLE = "gen9_agent_app"; // the role gen9-agent's API and worker connect as
const CANT_SAVE = "Gen9 can't save changes right now. Try again later.";
const NO_ANSWER = "Gen9's database didn't answer. Try again in a moment.";

let failures = 0;
function check(ok, what, detail = "") {
  console.log(`${ok ? "ok  " : "FAIL"}  ${what}${detail ? `: ${detail}` : ""}`);
  if (!ok) failures++;
  return ok;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const docker = (...args) => spawnSync("docker", args, { encoding: "utf8" });
const psql = (query) => docker("exec", POSTGRES, "psql", "-U", "postgres", "-d", "gen9_agent", "-tAc", query).stdout.trim();
// New sessions of the role take the setting; its open ones end, so the pools (pre-ping) reconnect
function readOnly(on) {
  psql(`alter role ${ROLE} ${on ? "set default_transaction_read_only = on" : "reset default_transaction_read_only"}`);
  psql(`select count(pg_terminate_backend(pid)) from pg_stat_activity where usename = '${ROLE}'`);
}
async function until(what, seconds, probe) {
  for (let i = 0; i < seconds; i++) {
    const value = await probe();
    if (value) return value;
    await sleep(1000);
  }
  throw new Error(`timed out waiting for ${what}`);
}

const dir = mkdtempSync(join(tmpdir(), "gen9-database-"));
async function api(method, path) {
  const token = JSON.parse(readFileSync(join(dir, "credentials.json"), "utf8")).access_token;
  const response = await fetch(`${API}${path}`, { method, headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" } });
  return { status: response.status, retryAfter: response.headers.get("retry-after"), body: await response.json().catch(() => null) };
}
const made = [];
let stopped = false;
try {
  check(Boolean(await signInTerminal({ email: env.GEN9_SEED_USER_EMAIL, password: env.GEN9_SEED_USER_PASSWORD, configDir: dir })), "the seeded user signs in on the terminal");
  check((await api("GET", "/v1/threads")).status === 200, "the chats read (200) before anything is changed");

  // 1. Writes refused, reads kept
  try {
    readOnly(true);
    // The first request after the pools' connections ended may meet one ending: ask until the
    // answer is the read-only one
    const refused = await until("a write refused as read-only", 20, async () => {
      const r = await api("POST", "/v1/threads");
      if (r.status === 201) made.push(r.body.id);
      return r.body?.detail === CANT_SAVE ? r : null;
    });
    const read = await api("GET", "/v1/threads");
    check(refused.status === 503 && refused.retryAfter === "30", "a new chat is refused with 503, Retry-After 30, in words", `${refused.status} ${refused.retryAfter} "${refused.body?.detail}"`);
    check(read.status === 200, "reading the chats still answers 200", `${read.status}`);
  } finally {
    readOnly(false);
  }
  const again = await until("a new chat once writable", 20, async () => {
    const r = await api("POST", "/v1/threads");
    if (r.status === 201) made.push(r.body.id);
    return r.status === 201 ? r : null;
  });
  check(again.status === 201, "writable again, a new chat is made", `${again.status}`);

  // 2. The database down
  let down;
  try {
    docker("stop", POSTGRES);
    stopped = true;
    down = await until("the API to say the database didn't answer", 30, async () => {
      const r = await api("GET", "/v1/threads");
      return r.body?.detail === NO_ANSWER ? r : null;
    });
  } finally {
    docker("start", POSTGRES);
    await until("gen9-postgres to be healthy", 90, () => docker("inspect", "-f", "{{.State.Health.Status}}", POSTGRES).stdout.trim() === "healthy");
    stopped = false;
  }
  check(down.status === 503 && down.retryAfter === "30", "with the database down, reading the chats answers 503, Retry-After 30, in words", `${down.status} ${down.retryAfter} "${down.body?.detail}"`);
  const back = await until("the API to answer again", 60, async () => ((await api("GET", "/v1/threads")).status === 200 ? true : null));
  check(back === true, "started again, the API answers by itself (200)");
  const chat = (await api("POST", "/v1/threads")).body?.id;
  const deleted = await api("DELETE", `/v1/threads/${chat}`);
  const gone = await until("the worker to delete the chat", 60, async () => (psql(`select count(*) from threads where id = '${chat}'`) === "0" ? true : null)).catch(() => false);
  check(deleted.status === 202 || deleted.status === 204 ? gone === true : false, "the worker too: a chat made and deleted, its deletion run to the end", `${deleted.status}; gone ${gone}`);
} catch (error) {
  check(false, "unexpected error", error.message.split("\n")[0]);
} finally {
  if (stopped) docker("start", POSTGRES);
  psql(`alter role ${ROLE} reset default_transaction_read_only`);
  for (const id of made) await api("DELETE", `/v1/threads/${id}`).catch(() => {});
  rmSync(dir, { recursive: true, force: true });
}
console.log(failures ? `\n${failures} check(s) failed` : "\nall checks passed");
process.exit(failures ? 1 : 0);
