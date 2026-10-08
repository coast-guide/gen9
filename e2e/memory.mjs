// A person's memory, through gen9-agent's API and `gen9 ask`:
//   1. a throwaway user (Admin API) signs in on the terminal and tells Gen9 to remember something:
//      the agent edits /memories/AGENTS.md (a step the chat shows as "Updated your memory")
//   2. /v1/me/memory shows it, and a new chat uses it
//   3. the seeded user's chat doesn't know it, and their memory doesn't have it
//   4. in Chrome, Settings > Memory shows it; editing it there changes what the next chat knows,
//      and what the chat of step 2 knows when asked again;
//      "Clear" asks first, then the next chat has forgotten (docs/design/screens/settings.md)
//   5. deleting the account deletes it from the store
// The user is deleted at the end, whatever happens. It reads the bootstrap admin and the seeded
// user from gen9-keycloak/.env, and the store from gen9-postgres (`docker exec`). It costs five
// short replies.
import { spawn, spawnSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { launch } from "./browser.mjs";
import { chatOf, deleteChats } from "./chats.mjs";
import { ROOT, signInTerminal } from "./signin.mjs";
import { forget } from "./forget.mjs";

const env = Object.fromEntries(
  readFileSync(`${ROOT}gen9-keycloak/.env`, "utf8")
    .split("\n")
    .filter((line) => /^[A-Z0-9_]+=/.test(line))
    .map((line) => [line.slice(0, line.indexOf("=")), line.slice(line.indexOf("=") + 1)]),
);
const API = process.env.GEN9_API ?? "http://localhost:17000";
const KEYCLOAK = process.env.KEYCLOAK_URL ?? "http://localhost:15000";
const APP = process.env.APP_URL ?? "http://localhost:14000";
const EMAIL = `memory-${Date.now()}@gen9.test`;
const PASSWORD = `e2e-${randomBytes(12).toString("hex")}`;
// A colour no one would guess, so "knows it" can't be luck
const COLOUR = `teal-${randomBytes(2).toString("hex")}`;
const ASK = "What is my favourite colour? Reply with exactly the colour, or exactly: Unknown.";

let failures = 0;
function check(ok, what, detail = "") {
  console.log(`${ok ? "ok  " : "FAIL"}  ${what}${detail ? `: ${detail}` : ""}`);
  if (!ok) failures++;
  return ok;
}

async function admin(path, init = {}) {
  // A fresh token each time: master's admin tokens last a minute, less than this check takes
  const token = await fetch(`${KEYCLOAK}/realms/master/protocol/openid-connect/token`, {
    method: "POST",
    body: new URLSearchParams({
      grant_type: "password",
      client_id: "admin-cli",
      username: env.KC_BOOTSTRAP_ADMIN_USERNAME,
      password: env.KC_BOOTSTRAP_ADMIN_PASSWORD,
    }),
  })
    .then((r) => r.json())
    .then((body) => body.access_token);
  const response = await fetch(`${KEYCLOAK}/admin/realms/gen9${path}`, {
    ...init,
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
  });
  if (!response.ok && response.status !== 404) throw new Error(`Keycloak ${init.method ?? "GET"} ${path}: ${response.status}`);
  return response.status === 201 || response.status === 204 || response.status === 404 ? null : response.json();
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
// The seeded user's chats, deleted at the end (the throwaway user's go with its account)
const seededChats = [];
const answer = async (configDir, question) => {
  const { out } = await gen9(configDir, "ask", question);
  if (configDir === seededDir) seededChats.push(chatOf(out));
  return out.split("\n")[0].trim();
};

async function token(configDir) {
  await gen9(configDir, "whoami");
  return JSON.parse(readFileSync(join(configDir, "credentials.json"), "utf8")).access_token;
}
async function api(bearer, method, path, body) {
  const response = await fetch(`${API}${path}`, {
    method,
    headers: { Authorization: `Bearer ${bearer}`, "Content-Type": "application/json" },
    body: body && JSON.stringify(body),
  });
  return { status: response.status, body: response.status === 204 ? null : await response.json() };
}

const userDir = mkdtempSync(join(tmpdir(), "gen9-memory-user-"));
const seededDir = mkdtempSync(join(tmpdir(), "gen9-memory-seeded-"));
let userId;
try {
  await admin("/users", {
    method: "POST",
    body: JSON.stringify({
      username: EMAIL,
      email: EMAIL,
      firstName: "Memory",
      lastName: "Check",
      enabled: true,
      emailVerified: true,
      credentials: [{ type: "password", value: PASSWORD, temporary: false }],
    }),
  });
  userId = (await admin(`/users?email=${encodeURIComponent(EMAIL)}&exact=true`))[0].id;
  const signedIn = await signInTerminal({ email: EMAIL, password: PASSWORD, configDir: userDir });
  check(Boolean(signedIn), "a throwaway user signs in on the terminal (device flow)", signedIn ?? "");
  const sub = psql(`select sub from users where email = '${EMAIL}'`);

  // 1. Remember
  const { out } = await gen9(userDir, "ask", `Remember that my favourite colour is ${COLOUR}. Reply with exactly: Noted.`);
  const thread = out.match(/--thread ([0-9a-f-]{36})/)?.[1];
  const steps = psql(
    `select string_agg(e.data->>'name' || ' ' || coalesce(e.data->'args'->>'file_path', ''), '; ') from run_events e` +
      ` join runs r on r.id = e.run_id where r.thread_id = '${thread}' and e.type = 'tool.started'`,
  );
  check(/(edit_file|write_file) \/memories\/AGENTS\.md/.test(steps), "told to remember, the agent edits the memory file", steps);

  // 2. See it, use it
  let bearer = await token(userDir);
  const seen = await api(bearer, "GET", "/v1/me/memory");
  check(seen.status === 200 && seen.body.content.includes(COLOUR) && seen.body.updated_at, "/v1/me/memory shows it, with when it changed", seen.body.content.trim());
  const recalledOut = (await gen9(userDir, "ask", ASK)).out;
  const recalled = recalledOut.split("\n")[0].trim();
  // Asked again after the edit in Settings (step 4): a chat already begun reads it anew
  const earlierChat = recalledOut.match(/--thread (\S+)/)?.[1];
  check(recalled.toLowerCase().includes(COLOUR), "a new chat uses it", recalled);

  // 3. Not for anyone else
  const seeded = await signInTerminal({ email: env.GEN9_SEED_USER_EMAIL, password: env.GEN9_SEED_USER_PASSWORD, configDir: seededDir });
  check(Boolean(seeded), "the seeded user signs in on the terminal", seeded ?? "");
  const theirs = await answer(seededDir, ASK);
  const theirMemory = await api(await token(seededDir), "GET", "/v1/me/memory");
  check(!theirs.toLowerCase().includes(COLOUR) && !theirMemory.body.content.includes(COLOUR), "the seeded user's chat and memory don't have it", theirs);

  // 4. Settings > Memory, in Chrome: see, edit, clear
  const browser = await launch({ headless: !process.env.HEADED, defaultViewport: { width: 1280, height: 900 } });
  try {
    const page = await browser.newPage();
    await page.goto(`${APP}/auth/login?returnTo=/settings`, { waitUntil: "networkidle0" });
    await page.type("#username", EMAIL);
    await page.type("#password", PASSWORD);
    await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }), page.click("#kc-login")]);
    if (!page.url().endsWith("/settings")) await page.goto(`${APP}/settings`, { waitUntil: "networkidle0" });
    const section = async () =>
      page.$$eval("main section", (els) => els.find((el) => el.querySelector("h2")?.textContent === "Memory")?.textContent ?? "");
    const button = async (label, scope = "main") => {
      const [el] = await page.$$(`xpath/.//${scope === "dialog" ? '*[@role="alertdialog"]' : "main"}//button[normalize-space()="${label}"]`);
      if (!el) throw new Error(`no button "${label}"`);
      await el.click();
    };
    const shown = await section();
    check(shown.includes(COLOUR) && /Updated/.test(shown), "Settings > Memory shows it, and when it changed", shown.slice(0, 90));
    await button("Edit");
    await page.waitForSelector("main textarea");
    const label = await page.$eval("main textarea", (el) => document.querySelector(`label[for="${el.id}"]`)?.textContent);
    await page.$eval("main textarea", (el) => el.select());
    await page.type("main textarea", "- The user's favourite colour is saffron.");
    await button("Save");
    await page.waitForFunction(() => document.body.textContent.includes("Memory saved."), { timeout: 15_000 });
    await page.waitForFunction(() => !document.querySelector("main textarea"), { timeout: 15_000 });
    check(label === "What Gen9 remembers" && (await section()).includes("saffron"), "Edit, then Save, shows the new memory and says so", label);
    const afterEdit = await answer(userDir, ASK);
    check(afterEdit.toLowerCase().includes("saffron"), "the next chat knows the edit", afterEdit);
    const sameChat = (await gen9(userDir, "ask", "--thread", earlierChat, ASK)).out.split("\n")[0].trim();
    check(sameChat.toLowerCase().includes("saffron"), "and so does the chat that knew the old one, asked again (P3-E4)", sameChat);
    await button("Clear");
    await page.waitForSelector('[role="alertdialog"]');
    const asked = await page.$eval('[role="alertdialog"]', (el) => el.textContent);
    await button("Clear memory", "dialog");
    await page.waitForFunction(() => document.body.textContent.includes("Nothing yet."), { timeout: 15_000 });
    check(/Clear everything Gen9 remembers/.test(asked) && /can’t undo/.test(asked), "Clear asks first, then shows nothing remembered", asked.slice(0, 60));
    const afterClear = await answer(userDir, ASK);
    const empty = await api(await token(userDir), "GET", "/v1/me/memory");
    check(empty.body.content === "" && /unknown/i.test(afterClear), "the next chat has forgotten", afterClear);
  } finally {
    await browser.close();
  }
  const tooLong = await api(await token(userDir), "PUT", "/v1/me/memory", { content: "x".repeat(16_001) });
  check(tooLong.status === 422, "an edit past 16,000 characters is refused", String(tooLong.status));

  // 5. The account goes, and its memory with it
  await gen9(userDir, "ask", `Remember that my favourite colour is ${COLOUR}. Reply with exactly: Noted.`);
  const before = psql(`select count(*) from langgraph.store where prefix = 'memories.${sub}'`);
  const account = await api(await token(userDir), "DELETE", "/v1/me");
  let after = "1";
  for (let i = 0; i < 60 && after !== "0"; i++) {
    after = psql(`select count(*) from langgraph.store where prefix = 'memories.${sub}'`);
    if (after !== "0") await new Promise((r) => setTimeout(r, 1000));
  }
  check(before !== "0" && [202, 204].includes(account.status) && after === "0", "deleting the account deletes its memory", `${before} item(s) before, DELETE /v1/me ${account.status}, ${after} after`);
} catch (e) {
  check(false, "the memory check ran to the end", e.message);
} finally {
  if (userId) await admin(`/users/${userId}`, { method: "DELETE" }).catch(() => {});
  forget(userId);
  if (seededChats.length) {
    const left = await deleteChats(seededDir, seededChats).catch((e) => [e.message]);
    check(!left.length, "the seeded user's chat is deleted", left.join(", "));
  }
  await gen9(seededDir, "logout").catch(() => {});
  rmSync(userDir, { recursive: true, force: true });
  rmSync(seededDir, { recursive: true, force: true });
}

console.log(failures ? `\n${failures} check(s) failed` : "\nall checks passed");
process.exit(failures ? 1 : 0);
