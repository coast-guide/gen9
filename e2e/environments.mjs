// A chat's environment (milestone 3): an OpenSandbox sandbox (gen9-sandbox) where the agent runs
// commands and keeps files, created on the chat's first command. What a command printed is read
// from the run's events in Postgres, not from the model's retelling.
//   1. in Chrome, as the seeded user: a chat runs Python in its environment; its step says what
//      ran; a container now serves that chat
//   2. a file written in one turn is there in the next
//   3. a host nobody allowed is unreachable from it (tried inside the chat's own container)
//   4. a secret for httpbin.org added in Settings: requests there from the running environment carry
//      it, its processes don't hold it; by default only reads (a GET) carry it, not a POST, and one
//      sent for changing carries it on a POST too; removed, requests go without it and the host
//      is closed again
//   5. a file the environment shares is under the answer and downloads as written; with the
//      environment removed, it still downloads and is still listed
//   6. a file attached in the composer (and with gen9 ask --attach) is put in the chat's
//      environment, and the answer reads it; the history shows it attached
//   6b. its limits (manual-e2e.md, P5-C5): Docker's seccomp filter, no new privileges, no raw
//      sockets or Docker socket, logs bounded, memory without swap; a process past 4,096 is
//      refused and one past its memory killed, the environment living on; a sandbox that writes
//      past SANDBOX_DISK_GB (10 GiB) is deleted with its sidecar and volume
//   4. another person's chat (the seeded admin, in the terminal) has an environment of its own,
//      without the first chat's file
//   5. "Ask before acting" (gen9 ask --ask-first): the command waits, shown as typed; y runs it
//   6. deleting a chat removes its container; deleting an account (a throwaway one) removes its
// Needs make up with gen9-sandbox. It deletes its chats and the throwaway account.
import { execFileSync, spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createRequire } from "node:module";
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
const KEYCLOAK = process.env.KEYCLOAK_URL ?? "http://localhost:15000";
const AXE = readFileSync(createRequire(import.meta.url).resolve("axe-core/axe.min.js"), "utf8");

let failures = 0;
function check(ok, what, detail = "") {
  console.log(`${ok ? "ok  " : "FAIL"}  ${what}${detail ? `: ${detail}` : ""}`);
  if (!ok) failures++;
  return ok;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const gen9 = (configDir, args, input) =>
  new Promise((resolve) => {
    const child = spawn("uv", ["run", "-q", "gen9", ...args], { cwd: `${ROOT}gen9-cli`, env: { ...process.env, GEN9_CONFIG_DIR: configDir } });
    let out = "";
    child.stdout.on("data", (d) => (out += d));
    child.stderr.on("data", (d) => (out += d));
    child.on("exit", (code) => resolve({ code, out }));
    child.stdin.end(input ?? "");
  });
async function api(configDir, method, path, body) {
  await gen9(configDir, ["whoami"]);
  const token = JSON.parse(readFileSync(join(configDir, "credentials.json"), "utf8")).access_token;
  const response = await fetch(`${API}${path}`, { method, headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  return { status: response.status, body: response.status === 204 || response.status === 202 ? null : await response.json().catch(() => null) };
}
async function admin(method, path, body) {
  const token = await fetch(`${KEYCLOAK}/realms/master/protocol/openid-connect/token`, {
    method: "POST",
    body: new URLSearchParams({ grant_type: "password", client_id: "admin-cli", username: env.KC_BOOTSTRAP_ADMIN_USERNAME, password: env.KC_BOOTSTRAP_ADMIN_PASSWORD }),
  })
    .then((r) => r.json())
    .then((b) => b.access_token);
  const response = await fetch(`${KEYCLOAK}/admin/realms/gen9${path}`, { method, headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  if (!response.ok && response.status !== 404) throw new Error(`Keycloak ${method} ${path}: ${response.status}`);
  const text = await response.text();
  return text ? JSON.parse(text) : null;
}
const psql = (sql) => execFileSync("docker", ["exec", "gen9-postgres-postgres-1", "psql", "-U", "postgres", "-d", "gen9_agent", "-tAc", sql], { encoding: "utf8" }).trim();
// What the chat's commands printed, in order (the run's events)
const commandOutputs = (thread) =>
  psql(
    `select coalesce(string_agg(e.data->>'output', ' ||| ' order by e.seq), '') from run_events e join runs r on r.id = e.run_id` +
      ` where r.thread_id = '${thread}' and e.type = 'tool.completed' and e.data->>'name' = 'execute'`,
  );
// The chat's sandboxes: containers OpenSandbox runs for it (its metadata becomes their labels)
const containersOf = (thread) =>
  execFileSync("docker", ["ps", "--filter", `label=gen9-thread=${thread}`, "--format", "{{.Names}}"], { encoding: "utf8" }).split("\n").filter(Boolean);
async function untilGone(thread) {
  for (let i = 0; i < 60 && containersOf(thread).length; i++) await sleep(1000);
  return containersOf(thread);
}
const threadOf = (out) => out.match(/--thread ([0-9a-f-]{36})/)?.[1];
async function buttonWithText(page, scope, text) {
  for (const b of await page.$$(`${scope} button`)) if ((await b.evaluate((e) => e.textContent.trim())) === text) return b;
  throw new Error(`no ${text} button in ${scope}`);
}
// A throwaway value for the secret check: httpbin.org echoes the request's headers
const SECRET = `e2e-${randomBytes(12).toString("hex")}`;
// A file to attach: its first line is unguessable, so an answer quoting it read it
const NOTE_FIRST_LINE = `lighthouse-${randomBytes(4).toString("hex")}`;
const NOTE_TEXT = `${NOTE_FIRST_LINE}\nsecond line\n`;
const NOTE_NAME = "attached-note.txt";
const NOTE = join(mkdtempSync(join(tmpdir(), "gen9-env-note-")), NOTE_NAME);
writeFileSync(NOTE, NOTE_TEXT);

const alan = mkdtempSync(join(tmpdir(), "gen9-env-alan-"));
const ada = mkdtempSync(join(tmpdir(), "gen9-env-ada-"));
const temp = mkdtempSync(join(tmpdir(), "gen9-env-temp-"));
const chats = [];
let tempId;
try {
  check(Boolean(await signInTerminal({ email: env.GEN9_SEED_USER_EMAIL, password: env.GEN9_SEED_USER_PASSWORD, configDir: alan })), "the seeded user signs in on the terminal");
  check(Boolean(await signInTerminal({ email: env.GEN9_SEED_ADMIN_EMAIL, password: env.GEN9_SEED_ADMIN_PASSWORD, configDir: ada })), "the seeded admin signs in on the terminal");

  const browser = await launch({ headless: !process.env.HEADED, defaultViewport: { width: 1280, height: 900 } });
  let thread;
  try {
    const page = await browser.newPage();
    await page.goto(`${APP}/auth/login?returnTo=/chat`, { waitUntil: "networkidle0" });
    await page.type("#username", env.GEN9_SEED_USER_EMAIL);
    await page.type("#password", env.GEN9_SEED_USER_PASSWORD);
    await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }), page.click("#kc-login")]);
    const turn = async (text) => {
      await page.type("#composer", text);
      await page.keyboard.press("Enter");
      await page.waitForSelector('button[aria-label="Stop"]', { timeout: 30_000 }).catch(() => {});
      await page.waitForFunction(() => !document.querySelector('button[aria-label="Stop"]'), { timeout: 300_000, polling: 500 });
      return page.$$eval("ol[aria-live] > li", (lis) => lis.at(-1)?.textContent ?? "");
    };

    // 1. A command in the chat's environment
    await page.goto(`${APP}/chat`, { waitUntil: "networkidle0" });
    const first = await turn("Run this with python3 in your environment and tell me what it printed: print(6 * 7). Then write the word harbour into the file /work/note.txt there.");
    thread = page.url().match(/\/chat\/([0-9a-f-]{36})/)?.[1];
    chats.push(thread);
    const outputs = commandOutputs(thread);
    const boxes = containersOf(thread);
    const ran = first.match(/Ran: [^\n]{0,60}/)?.[0] ?? "";
    const attempts = psql(`select coalesce(string_agg(e.data->>'attempt', ','), '') from run_events e join runs r on r.id = e.run_id where r.thread_id = '${thread}' and e.type = 'run.started'`);
    const names = psql(`select coalesce(string_agg(e.data->>'name', ',' order by e.seq), '') from run_events e join runs r on r.id = e.run_id where r.thread_id = '${thread}' and e.type = 'tool.started'`);
    check(/\b42\b/.test(outputs) && ran.length > 0 && boxes.length >= 1, "a chat runs Python in its environment, its step says what ran, and a container serves it", `${ran || `steps: ${first.slice(0, 160)}`}; printed 42: ${/\b42\b/.test(outputs)}; ${boxes.length} container(s); attempts ${attempts}; tools ${names}`);

    // 2. The next turn finds the file
    const second = await turn("In your environment, read /work/note.txt and tell me exactly what it says.");
    check(/harbour/i.test(second), "a file written in one turn is there in the next", second.slice(0, 90));

    // 3. No way out to a host nobody allowed: tried inside the chat's own container, as its
    // commands run (the model may decline to try, which isn't what this checks)
    const [box] = containersOf(thread);
    const outbound = execFileSync("docker", ["exec", box, "python3", "-c", "import urllib.request\ntry:\n print(urllib.request.urlopen('https://example.com', timeout=8).status)\nexcept Exception as e:\n print('blocked:', type(e).__name__, e)"], { encoding: "utf8" }).trim();
    check(outbound.startsWith("blocked:"), "a host nobody allowed is unreachable from the chat's environment", outbound.slice(0, 120));

    // 4. A secret for a host, added in Settings, reaches the running environment on its way out
    const echo = (method = "GET") =>
      execFileSync("docker", ["exec", box, "python3", "-c", `import urllib.request, json\ntry:\n r = urllib.request.urlopen(urllib.request.Request('https://httpbin.org/anything', method='${method}', data=b'x' if '${method}' == 'POST' else None), timeout=10)\n print('auth:', json.loads(r.read())['headers'].get('Authorization', 'none'))\nexcept Exception as e:\n print('blocked:', type(e).__name__)`], { encoding: "utf8" }).trim();
    const until = async (wanted, method) => {
      let seen = "";
      for (let i = 0; i < 30; i++) {
        seen = echo(method);
        if (wanted(seen)) break;
        await sleep(1000);
      }
      return seen;
    };
    const form = 'form[aria-label="Add a secret"]';
    // Added in Settings as a person would, "Sent for" left as it is unless `forChanges`
    const addEcho = async (forChanges) => {
      await page.goto(`${APP}/settings`, { waitUntil: "networkidle0" });
      await (await buttonWithText(page, "main", "Add a secret")).click();
      await page.waitForSelector(form);
      const inputs = await page.$$(`${form} input`);
      await inputs[0].type("echo");
      await inputs[1].type("httpbin.org");
      if (forChanges) await page.select(`${form} select[id$="-methods"]`, "all");
      await page.type(`${form} input[type=password]`, SECRET);
      await (await buttonWithText(page, form, "Add")).click();
      await page.waitForFunction(() => [...document.querySelectorAll("main p")].some((p) => p.textContent === "echo"), { timeout: 30_000 });
      return page.$$eval("main p", (ps) => ps[ps.findIndex((p) => p.textContent === "echo") + 1]?.textContent ?? "");
    };
    const removeEcho = async () => {
      const [removeSecret] = await page.$$('button[aria-label="Remove echo"]');
      await removeSecret.click();
      await page.waitForSelector('[role="alertdialog"]');
      await (await buttonWithText(page, '[role="alertdialog"]', "Remove")).click();
      await page.waitForFunction(() => ![...document.querySelectorAll("main p")].some((p) => p.textContent === "echo"), { timeout: 30_000 });
    };
    const row = await addEcho(false);
    const carried = await until((seen) => seen.includes(SECRET));
    const inside = execFileSync("docker", ["exec", box, "sh", "-c", "cat /proc/1/environ | tr '\\0' '\\n'; env"], { encoding: "utf8" });
    check(carried === `auth: Bearer ${SECRET}` && !inside.includes(SECRET), "a secret added in Settings reaches the running environment's requests to its host, never its processes", carried.replace(SECRET, "<the secret>"));
    const posted = echo("POST");
    check(row.endsWith("· reading only") && posted === "auth: none", "by default only reads carry it: a POST from the environment goes without it", `${row}; POST ${posted.replace(SECRET, "<the secret>")}`);
    await removeEcho();
    const after = await until((seen) => seen.startsWith("blocked"));
    check(after.startsWith("blocked"), "removed in Settings, the environment's requests go without it, and the host is closed again", after);
    const rowForChanges = await addEcho(true);
    const postedForChanges = await until((seen) => seen.includes(SECRET), "POST");
    check(rowForChanges.endsWith("· reading and changing") && postedForChanges === `auth: Bearer ${SECRET}`, "sent for changing too, a POST carries it", `${rowForChanges}; POST ${postedForChanges.replace(SECRET, "<the secret>")}`);
    await removeEcho();
    const closed = await until((seen) => seen.startsWith("blocked"));
    check(closed.startsWith("blocked"), "removed again, the host is closed again", closed);

    // 5. A file the environment shares: under the answer, downloadable, and still after the
    // environment is gone
    await page.goto(`${APP}/chat/${thread}`, { waitUntil: "networkidle0" });
    await turn("In your environment, make a CSV with the header name,score and the rows ada,3 and alan,5, and share it with me as scores.csv.");
    await page.waitForSelector('ul[aria-label="Files"] a', { timeout: 30_000 });
    const link = await page.$$eval('ul[aria-label="Files"] a', (as) => as.map((a) => ({ href: a.getAttribute("href"), text: a.textContent })).find((l) => /scores\.csv/.test(l.text)));
    const download = () =>
      page.evaluate(async (href) => {
        const r = await fetch(href);
        return { status: r.status, disposition: r.headers.get("content-disposition") ?? "", body: await r.text() };
      }, link?.href);
    const inEnvironment = execFileSync("docker", ["exec", box, "cat", "/work/out/scores.csv"], { encoding: "utf8" });
    const got = await download();
    check(Boolean(link) && got.status === 200 && got.body === inEnvironment && got.disposition.startsWith("attachment") && /ada,3/.test(got.body), "a file the environment shares is under the answer, and downloads as it was written", `${link?.text ?? "no scores.csv"}; ${got.status} ${got.disposition}`);
    // Its environment removed, as after half an hour unused (OpenSandbox's own API)
    const sandboxId = execFileSync("docker", ["inspect", box, "--format", '{{index .Config.Labels "opensandbox.io/id"}}'], { encoding: "utf8" }).trim();
    const sandboxKey = readFileSync(`${ROOT}gen9-sandbox/.env`, "utf8").match(/^SANDBOX_API_KEY=(.*)$/m)?.[1];
    await fetch(`http://127.0.0.1:20000/v1/sandboxes/${sandboxId}`, { method: "DELETE", headers: { "OPEN-SANDBOX-API-KEY": sandboxKey } });
    const leftover = await untilGone(thread);
    const later = await download();
    await page.reload({ waitUntil: "networkidle0" });
    const listed = await page.$$eval('ul[aria-label="Files"] a', (as) => as.map((a) => a.textContent));
    check(leftover.length === 0 && later.status === 200 && later.body === inEnvironment && listed.some((t) => /scores\.csv/.test(t)), "with the environment gone, it still downloads, and the chat still lists it", `${leftover.length} container(s) left; ${later.status}; listed: ${listed.join(", ")}`);

    // 6. A file attached in the composer: in the chat's environment, and read there
    await page.goto(`${APP}/chat`, { waitUntil: "networkidle0" });
    const [chooser] = await Promise.all([page.waitForFileChooser(), page.click('button[aria-label="Attach files"]')]);
    await chooser.accept([NOTE]);
    await page.waitForFunction(() => document.querySelector('ul[aria-label="Attached files"] li') && !document.querySelector('[aria-label="Attaching"]'), { timeout: 30_000 });
    const chip = await page.$eval('ul[aria-label="Attached files"]', (u) => u.textContent);
    await page.evaluate(AXE);
    const { violations } = await page.evaluate(() => window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] } }));
    const blocking = violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    check(!blocking.length, "the composer with a file attached has no serious accessibility violations", blocking.map((v) => v.id).join(", "));
    const read = await turn("What is the first line of the file I attached? Reply with that line exactly.");
    const attachedThread = page.url().match(/\/chat\/([0-9a-f-]{36})/)?.[1];
    chats.push(attachedThread);
    const [attachedBox] = containersOf(attachedThread);
    const there = attachedBox ? execFileSync("docker", ["exec", attachedBox, "cat", `/work/in/${NOTE_NAME}`], { encoding: "utf8" }) : "";
    check(chip.includes(NOTE_NAME) && there === NOTE_TEXT && read.includes(NOTE_FIRST_LINE), "a file attached in the composer is in the chat's environment, and the answer reads it", `${read.replace(/\s+/g, " ").slice(0, 90)}`);
    await page.reload({ waitUntil: "networkidle0" });
    const question = await page.$$eval("ol[aria-live] > li", (lis) => lis[0]?.textContent ?? "");
    check(question.includes(`Attached: ${NOTE_NAME}`) && !question.includes("/work/in"), "after a reload, the question shows what was attached, as written", question.slice(0, 120));

    // 6b. Its limits, in the attached chat's environment (gen9-sandbox's config.toml and launch.py)
    const run = (script) => execFileSync("docker", ["exec", attachedBox, "sh", "-c", script], { encoding: "utf8" }).trim();
    const host = JSON.parse(execFileSync("docker", ["inspect", attachedBox], { encoding: "utf8" }))[0].HostConfig;
    const status = run("grep -E '^(Seccomp|NoNewPrivs):' /proc/self/status | tr -s '\\t ' ' ' | tr '\\n' ' '; ls /var/run/docker.sock /run/docker.sock 2>&1 | grep -c 'No such'");
    check(
      /Seccomp: 2/.test(status) && /NoNewPrivs: 1/.test(status) && status.endsWith(" 2") && host.CapDrop.includes("NET_RAW") && host.LogConfig.Type === "local" && host.LogConfig.Config["max-size"] === "10m" && host.MemorySwap === host.Memory,
      "its environment runs under Docker's seccomp filter, no new privileges, no raw sockets or Docker socket, bounded logs, and memory without swap",
      `${status}; log ${host.LogConfig.Type} ${host.LogConfig.Config["max-size"]}; memory ${host.Memory} swap ${host.MemorySwap}`,
    );
    const spawned = run(`timeout 60 python3 -c "
import subprocess
ps = []
try:
    while len(ps) < 6000:
        ps.append(subprocess.Popen(['sleep', '30']))
except OSError:
    pass
for p in ps: p.kill()
print(len(ps))"`);
    const memory = run("python3 -c 'b = bytearray(3 * 1024**3)' 2>/dev/null; echo exit=$?");
    const alive = run("echo alive");
    check(Number(spawned) < 4096 && Number(spawned) > 1000 && memory === "exit=137" && alive === "alive", "a process past its 4,096 is refused and one past its memory is killed, and the environment lives on", `${spawned} processes; memory ${memory}; ${alive}`);
    const free = Number(run("df -Pk / | tail -1 | tr -s ' ' | cut -d' ' -f4")) / 1024 ** 2;
    if (free < 40) console.log(`skip  a sandbox writing past 10 GiB is deleted: only ${free.toFixed(0)} GiB free here, and it writes 11`);
    else {
      const sandboxOf = JSON.parse(execFileSync("docker", ["inspect", attachedBox], { encoding: "utf8" }))[0].Config.Labels["opensandbox.io/id"];
      run("fallocate -l 11G /work/big");
      const gone = await untilGone(attachedThread);
      // The server removes the container, then its sidecar and volume a moment later
      const leftOf = () => [
        ...execFileSync("docker", ["ps", "-a", "--format", "{{.Names}}"], { encoding: "utf8" }).split("\n").filter((n) => n.includes(sandboxOf)),
        ...execFileSync("docker", ["volume", "ls", "--format", "{{.Name}}"], { encoding: "utf8" }).split("\n").filter((v) => v.includes(sandboxOf)),
      ];
      for (let i = 0; i < 30 && leftOf().length; i++) await sleep(1000);
      const [sidecars, volumes] = [leftOf(), []];
      check(gone.length === 0 && volumes.length === 0 && sidecars.length === 0, "a sandbox that writes past SANDBOX_DISK_GB is deleted, with its sidecar and volume", `left: ${[...gone, ...sidecars, ...volumes].join(", ") || "nothing"}`);
    }
  } finally {
    await browser.close();
  }

  // 4. Another person's chat, an environment of its own
  const other = await gen9(ada, ["ask", "In your environment, run: ls /work/note.txt. Tell me exactly what it printed."]);
  const otherThread = threadOf(other.out);
  chats.push(otherThread);
  const otherOutputs = commandOutputs(otherThread);
  const otherBoxes = containersOf(otherThread);
  check(otherBoxes.length >= 1 && !otherBoxes.some((b) => containersOf(thread).includes(b)) && /No such file/i.test(otherOutputs), "another person's chat has an environment of its own, without the first chat's file", otherOutputs.slice(0, 80));

  // 5. Ask before acting: the command waits, as typed
  const asked = await gen9(ada, ["ask", "--ask-first", "Run python3 -c 'print(3 + 4)' in your environment and tell me the result."], "y\n");
  chats.push(threadOf(asked.out));
  check(asked.code === 0 && asked.out.includes("Gen9 wants to run a command in this chat's environment") && /\$ python3 -c/.test(asked.out) && /\b7\b/.test(commandOutputs(threadOf(asked.out))), "in Ask before acting, the command waits, shown as typed; y runs it", asked.out.match(/\$ [^\n]*/)?.[0] ?? asked.out.slice(0, 120));

  // In the terminal: gen9 ask --attach
  const cliAttached = await gen9(ada, ["ask", "--attach", NOTE, "What is the first line of the file I attached? Reply with that line exactly."]);
  chats.push(threadOf(cliAttached.out));
  check(cliAttached.code === 0 && cliAttached.out.includes(NOTE_FIRST_LINE), "gen9 ask --attach puts a file in the chat's environment, and the answer reads it", cliAttached.out.replace(/\s+/g, " ").slice(0, 100));

  // 7. Deleting a chat, then an account, removes their environments
  const deleted = await api(alan, "DELETE", `/v1/threads/${thread}`);
  const left = await untilGone(thread);
  check([202, 204].includes(deleted.status) && left.length === 0, "deleting the chat removes its container", `DELETE ${deleted.status}; ${left.length} left`);
  const email = `environments-${Date.now()}@gen9.test`;
  const password = `e2e-${randomBytes(12).toString("hex")}`;
  await admin("POST", "/users", { username: email, email, firstName: "Env", lastName: "Check", enabled: true, emailVerified: true, credentials: [{ type: "password", value: password, temporary: false }] });
  tempId = (await admin("GET", `/users?email=${encodeURIComponent(email)}&exact=true`))[0].id;
  check(Boolean(await signInTerminal({ email, password, configDir: temp })), "a throwaway account signs in on the terminal");
  const theirs = threadOf((await gen9(temp, ["ask", "Run python3 -c 'print(5)' in your environment and tell me what it printed."])).out);
  const before = containersOf(theirs).length;
  const gone = await api(temp, "DELETE", "/v1/me");
  const after = await untilGone(theirs);
  check(before >= 1 && [202, 204].includes(gone.status) && after.length === 0, "deleting an account removes its environments", `${before} before, DELETE /v1/me ${gone.status}, ${after.length} after`);
} catch (e) {
  check(false, "the environments check ran to the end", e.message);
} finally {
  for (const chat of chats.filter(Boolean)) {
    await api(alan, "DELETE", `/v1/threads/${chat}`).catch(() => {});
    await api(ada, "DELETE", `/v1/threads/${chat}`).catch(() => {});
  }
  if (tempId) await admin("DELETE", `/users/${tempId}`).catch(() => {});
  for (const dir of [alan, ada]) await gen9(dir, ["logout"]).catch(() => {});
  for (const dir of [alan, ada, temp]) rmSync(dir, { recursive: true, force: true });
}

console.log(failures ? `\n${failures} check(s) failed` : "\nall checks passed");
process.exit(failures ? 1 : 0);
