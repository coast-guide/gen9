// A person's data as one ZIP (GDPR Art. 20; gen9-agent's api/export.py; manual-e2e.md, P3-E1). As a
// throwaway user with a chat that holds an attached file, a memory line, a scheduled task and an
// environment secret, in Chrome: Settings' "Download" gives a ZIP whose JSON says what the API
// says (the chat's question and answer, the memory, the task, the secret's name, host and methods), with
// the attached file byte for byte, and no secret's value anywhere in it. Another person gets 401
// without signing in; the export is in the audit log. It deletes the user at the end, whatever
// happens; the chat's answer is its one model call.
import { execFileSync, spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { FIREFOX, launch } from "./browser.mjs";
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
const EMAIL = `export-${Date.now()}@gen9.test`;
const PASSWORD = `e2e-${randomBytes(12).toString("hex")}`;
const TAG = randomBytes(4).toString("hex");
const SECRET_VALUE = `never-exported-${randomBytes(12).toString("hex")}`;
const NOTE = `lighthouse-${TAG}\nsecond line\n`;

let failures = 0;
function check(ok, what, detail = "") {
  if (!ok) failures++;
  console.log(`${ok ? "ok  " : "FAIL"}  ${what}${detail ? `: ${detail}` : ""}`);
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

const dir = mkdtempSync(join(tmpdir(), "gen9-export-"));
const downloads = join(dir, "downloads");
// Firefox saves the download into that folder by its own settings (it has no DevTools protocol)
const browser = await launch({
  headless: !process.env.HEADED,
  extraPrefsFirefox: { "browser.download.folderList": 2, "browser.download.dir": downloads, "browser.download.useDownloadDir": true, "browser.download.always_ask_before_handling_new_types": false },
});
let id;
try {
  await admin("POST", "/users", { username: EMAIL, email: EMAIL, firstName: "Export", lastName: "Check", enabled: true, emailVerified: true, credentials: [{ type: "password", value: PASSWORD, temporary: false }] });
  id = (await admin("GET", `/users?email=${encodeURIComponent(EMAIL)}&exact=true`))[0]?.id;
  const config = join(dir, "config");
  check(Boolean(await signInTerminal({ email: EMAIL, password: PASSWORD, configDir: config })), "a throwaway user signs in on the terminal");
  const token = JSON.parse(readFileSync(join(config, "credentials.json"), "utf8")).access_token;
  const api = (method, path, body) =>
    fetch(`${API}${path}`, { method, headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });

  // What they give Gen9: a chat with an attached file, a memory line, a task, a secret
  const note = join(dir, "note.txt");
  writeFileSync(note, NOTE);
  const asked = await new Promise((resolve) => {
    const child = spawn("uv", ["run", "-q", "gen9", "ask", "--attach", note, "What is the first line of the file I attached? Reply with that line exactly."], { cwd: `${ROOT}gen9-cli`, env: { ...process.env, GEN9_CONFIG_DIR: config } });
    let out = "";
    child.stdout.on("data", (d) => (out += d));
    child.stderr.on("data", (d) => (out += d));
    child.on("close", () => resolve(out));
  });
  const thread = asked.match(/--thread (\S+)/)?.[1];
  await api("PUT", "/v1/me/memory", { content: `- Export check ${TAG}` });
  const task = await api("POST", "/v1/tasks", { name: `export ${TAG}`, prompt: "Reply with one word: ok", schedule: { kind: "once", time: "10:00", date: "2030-06-01" }, time_zone: "UTC" });
  const secret = await api("POST", "/v1/me/environment-secrets", { name: `export-${TAG}`, host: "api.example.com", value: SECRET_VALUE });
  check(Boolean(thread) && asked.includes(`lighthouse-${TAG}`) && task.ok && secret.ok, "a chat with a file, memory, a task and a secret to export", `${thread}; task ${task.status}; secret ${secret.status}`);

  // Settings, "Download"
  const page = await browser.newPage();
  let finished;
  if (FIREFOX) {
    // Done when the ZIP is there and Firefox's partial file is gone
    finished = (async () => {
      mkdirSync(downloads, { recursive: true });
      for (;;) {
        const files = readdirSync(downloads);
        if (files.some((f) => f.endsWith(".zip")) && !files.some((f) => f.endsWith(".part"))) return;
        await new Promise((r) => setTimeout(r, 250));
      }
    })();
  } else {
    const cdp = await page.createCDPSession();
    await cdp.send("Browser.setDownloadBehavior", { behavior: "allow", downloadPath: downloads, eventsEnabled: true });
    finished = new Promise((resolve) => cdp.on("Browser.downloadProgress", (e) => e.state === "completed" && resolve(e)));
  }
  await page.goto(`${APP}/auth/login?returnTo=/settings`, { waitUntil: "networkidle0" });
  await page.type("#username", EMAIL);
  await page.type("#password", PASSWORD);
  await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }), page.click("#kc-login")]);
  const started = Date.now();
  await page.click('a[href="/api/export"]');
  await Promise.race([finished, new Promise((_, reject) => setTimeout(() => reject(new Error("no download in 60 s")), 60_000))]);
  const [zipName] = readdirSync(downloads).filter((f) => f.endsWith(".zip"));
  check(/^gen9-export-\d{4}-\d{2}-\d{2}\.zip$/.test(zipName ?? ""), "Settings' Download gives a dated ZIP", `${zipName}, ${Date.now() - started} ms`);

  const out = join(dir, "unzipped");
  execFileSync("unzip", ["-q", join(downloads, zipName), "-d", out]);
  const read = (name) => readFileSync(join(out, name), "utf8");
  const listed = execFileSync("unzip", ["-Z1", join(downloads, zipName)], { encoding: "utf8" }).trim().split("\n");
  check(
    ["README.txt", "account.json", "conversations.json", "memory.md", "tasks.json", "connectors.json", "environment-secrets.json", "plugins.json"].every((f) => listed.includes(f)),
    "it holds the README and each part",
    listed.filter((f) => !f.startsWith("files/")).join(", "),
  );
  const account = JSON.parse(read("account.json"));
  const chats = JSON.parse(read("conversations.json"));
  const chat = chats.find((c) => c.id === thread);
  const said = (chat?.messages ?? []).map((m) => `${m.role}: ${m.content}`).join(" | ");
  check(account.email === EMAIL && account.notifications?.email && account.controls, "account.json: the account, notifications and controls", `${account.email}`);
  check(Boolean(chat) && said.includes("first line of the file") && said.includes(`lighthouse-${TAG}`), "conversations.json: the chat's question and answer, as Gen9 shows them", said.slice(0, 140));
  check(read("memory.md").includes(`Export check ${TAG}`), "memory.md: what Gen9 remembers");
  check(JSON.parse(read("tasks.json")).some((t) => t.name === `export ${TAG}`), "tasks.json: the scheduled task");
  const secrets = JSON.parse(read("environment-secrets.json"));
  check(secrets.some((s) => s.name === `export-${TAG}` && s.host === "api.example.com" && s.methods === "read"), "environment-secrets.json: the secret's name and host, and that it goes with reads only");
  const attached = listed.find((f) => f.startsWith(`files/${thread}/`) && f.endsWith("note.txt"));
  check(Boolean(attached) && read(attached) === NOTE, "the attached file, byte for byte", attached);
  const everything = execFileSync("unzip", ["-p", join(downloads, zipName)]).toString("latin1");
  check(!everything.includes(SECRET_VALUE), "no secret's value anywhere in the ZIP");

  // Not without signing in; and recorded
  const anonymous = await fetch(`${API}/v1/me/export`);
  const viaApp = await fetch(`${APP}/api/export`, { redirect: "manual" });
  check(anonymous.status === 401 && viaApp.status === 401, "without a sign-in: 401 from the API and the app", `${anonymous.status}, ${viaApp.status}`);
  const recorded = psql(`select detail->>'chats' || ' chats, ' || (detail->>'files') || ' files' from audit_events where action = 'account.export' and actor = (select sub from users where email = '${EMAIL}') order by at desc limit 1`);
  check(/^\d+ chats, 1 files$/.test(recorded), "the export is in the audit log", recorded);
} catch (e) {
  check(false, "the export check ran to the end", e.message);
} finally {
  await browser.close();
  rmSync(dir, { recursive: true, force: true });
  // gen9-agent's sweep removes their Gen9 data once they're gone from Keycloak
  if (id) await admin("DELETE", `/users/${id}`).catch(() => {});
}
console.log(failures ? `\n${failures} check(s) failed` : "\nall checks passed");
process.exit(failures ? 1 : 0);
