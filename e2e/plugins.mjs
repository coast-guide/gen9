// Plugin sources (gen9-agent/README.md, "Plugins"; docs/design/screens/admin-plugins.md): an admin adds
// a git repository holding a plugin marketplace, and the worker fetches it, hardened, and keeps what
// each plugin brings. Against a git server of its own (fixtures/git-server.mjs on :17805, which the
// worker reaches as host.docker.internal; PLUGIN_SOURCES_ALLOWED_HOSTS, make setup adds it). As the
// seeded admin, in Chrome on Admin > Plugins (the terminal's tokens carry no admin role), with what
// Gen9 kept read from Postgres:
//   1. a marketplace in Codex's format lists six plugins, one of each kind:
//      - an Agent Plugins one (a skill, a remote MCP server, a stdio one): loaded;
//      - a Claude Code one whose skill is named unlike its folder: loaded, with a note;
//      - a broken one: rejected, with why;
//      - one in another repository (url): loaded;
//      - one in a monorepo's folder (git-subdir, a sparse fetch): loaded;
//      - one from npm: unsupported, with why.
//   2. every new plugin starts off; an admin can make a loaded one available, not a broken one
//   3. a change pushed to the repository, then Sync now: the new skill is kept, the plugin taken
//      out of the marketplace goes, and the one in another repository isn't fetched again (its
//      commit didn't move). The changed plugin, still available, reaches nobody until the admin
//      looks: its row says so with its commit, and "Let people have it again" ends that
//   3b. the repository goes away: Sync now fails, and the row still says when it last synced and
//      how many plugins it offers; back, it syncs again
//   4. a marketplace in Claude Code's format, whose entry is the manifest, syncs too
//   5. refused: plain http to a host nobody named, a private address, a redirect, and a
//      repository without a marketplace, each saying why
//   6. a person's plugins: the seeded user adds the plugin in Settings, and their chat follows its
//      skill (an unguessable phrase), the step naming the plugin, also after a reload. Its remote
//      MCP server becomes their connector ("From e2e-portable", no Remove of its own), whose call
//      waits for Allow and then runs. The admin, who didn't add it, has neither until it's made
//      "Everyone". A change synced while everyone has it: their skill list drops it and Settings
//      says it waits for an admin, until the admin lets people have it again. Removed, the skill
//      is gone from the next message of the same chat, and the connector is gone
//   7. the screen has no serious accessibility violations; the seeded user can't reach the API
//   8. removing a source (after confirming) removes its plugins and their files
import { execFileSync, spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { createRequire } from "node:module";
import { launch } from "./browser.mjs";
import { serveGit } from "./fixtures/git-server.mjs";
import { ROOT, signInTerminal } from "./signin.mjs";
import { secondStep } from "./second-step.mjs";

const env = Object.fromEntries(
  readFileSync(`${ROOT}gen9-keycloak/.env`, "utf8")
    .split("\n")
    .filter((line) => /^[A-Z0-9_]+=/.test(line))
    .map((line) => [line.slice(0, line.indexOf("=")), line.slice(line.indexOf("=") + 1)]),
);
const API = process.env.GEN9_API ?? "http://localhost:17000";
const APP = process.env.APP_URL ?? "http://localhost:14000";
const AXE = readFileSync(createRequire(import.meta.url).resolve("axe-core/axe.min.js"), "utf8");
const PORT = 17805;
const GIT = `http://host.docker.internal:${PORT}`;
const SCHEMA = "https://agent-plugins.org/schemas/1.0.0";

let failures = 0;
function check(ok, what, detail = "") {
  console.log(`${ok ? "ok  " : "FAIL"}  ${what}${detail ? `: ${detail}` : ""}`);
  if (!ok) failures++;
  return ok;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
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
const psql = (sql) => execFileSync("docker", ["exec", "gen9-postgres-postgres-1", "psql", "-U", "postgres", "-d", "gen9_agent", "-tAc", sql], { encoding: "utf8" }).trim();

// Repositories: written in `work`, served bare from `served`
const base = mkdtempSync(join(tmpdir(), "gen9-plugins-"));
const work = join(base, "work");
const served = join(base, "served");
mkdirSync(served, { recursive: true });
const git = (cwd, ...args) => execFileSync("git", ["-c", "user.name=e2e", "-c", "user.email=e2e@gen9.test", "-c", "init.defaultBranch=main", ...args], { cwd, encoding: "utf8", env: { ...process.env, GIT_CONFIG_NOSYSTEM: "1" } });
function write(repo, files) {
  for (const [path, content] of Object.entries(files)) {
    const file = join(work, repo, path);
    mkdirSync(dirname(file), { recursive: true });
    writeFileSync(file, typeof content === "string" ? content : JSON.stringify(content, null, 2));
  }
}
function publish(repo, files, message) {
  const dir = join(work, repo);
  const fresh = !existsSync(dir);
  if (fresh) {
    mkdirSync(dir, { recursive: true });
    git(dir, "init", "-q");
  }
  write(repo, files);
  git(dir, "add", "-A");
  git(dir, "commit", "-qm", message);
  if (fresh) git(base, "clone", "-q", "--bare", dir, join(served, `${repo}.git`));
  else git(dir, "push", "-q", join(served, `${repo}.git`), "HEAD:main");
  return git(dir, "rev-parse", "HEAD").trim();
}
// The greeting skill's phrase: unguessable, so an answer holding it read the skill
const PHRASE = `tangerine-${randomBytes(4).toString("hex")}`;
const ASK = "Greet me the e2e-portable way, following your skill for it.";
const skill = (name, description) => `---\nname: ${name}\ndescription: ${description}\n---\nFollow these steps.\n`;
const agentPlugin = (name) => ({ $schema: `${SCHEMA}/plugin.schema.json`, name, version: "1.0.0", description: `The ${name} plugin` });

publish("extra", { "plugin.json": agentPlugin("e2e-extra"), "skills/e2e-extra-skill/SKILL.md": skill("e2e-extra-skill", "A skill from another repository") }, "extra");
publish(
  "mono",
  {
    "tools/e2e-sub/plugin.json": agentPlugin("e2e-sub"),
    "tools/e2e-sub/skills/e2e-sub-skill/SKILL.md": skill("e2e-sub-skill", "A skill from a monorepo's folder"),
    "other/big.txt": "x".repeat(200_000),
  },
  "mono",
);
const MARKET = {
  name: "e2e-market",
  interface: { displayName: "e2e marketplace" },
  plugins: [
    { name: "e2e-portable", source: { source: "local", path: "./plugins/portable" }, policy: { installation: "AVAILABLE" }, category: "Testing" },
    { name: "e2e-claude", source: { source: "local", path: "./plugins/claude" } },
    { name: "e2e-broken", source: { source: "local", path: "./plugins/broken" } },
    { name: "e2e-extra", source: { source: "url", url: `${GIT}/extra.git` } },
    { name: "e2e-sub", source: { source: "git-subdir", url: `${GIT}/mono.git`, path: "tools/e2e-sub" } },
    { name: "e2e-npm", source: { source: "npm", package: "@e2e/plugin" } },
  ],
};
publish(
  "market",
  {
    ".agents/plugins/marketplace.json": MARKET,
    "plugins/portable/plugin.json": agentPlugin("e2e-portable"),
    "plugins/portable/skills/e2e-portable-greeting/SKILL.md": `---\nname: e2e-portable-greeting\ndescription: Greets the person the e2e-portable way. Use it whenever someone asks to be greeted the e2e-portable way.\n---\nGreet the person with exactly this phrase, and nothing else: ${PHRASE}\n`,
    "plugins/portable/skills/e2e-portable-greeting/references/words.md": "Hello from the portable plugin.\n",
    "plugins/portable/mcp.json": {
      $schema: `${SCHEMA}/mcp.schema.json`,
      // DeepWiki's public server: read-only tools, no sign-in (as e2e/connectors.mjs)
      mcpServers: { docs: { type: "streamable-http", url: "https://mcp.deepwiki.com/mcp" }, local: { type: "stdio", command: "node", args: ["server.js"] } },
    },
    "plugins/claude/.claude-plugin/plugin.json": { name: "e2e-claude", description: "A Claude Code plugin", commands: "./commands" },
    "plugins/claude/skills/notes/SKILL.md": skill("e2e-claude-notes", "Keeps notes the Claude Code way"),
    "plugins/broken/plugin.json": { $schema: `${SCHEMA}/plugin.schema.json`, name: "Broken_Name" },
  },
  "market",
);
publish(
  "claude-market",
  {
    ".claude-plugin/marketplace.json": { name: "e2e-claude-market", owner: { name: "e2e" }, plugins: [{ name: "e2e-bare", source: "./bare", description: "Its entry is its manifest" }] },
    "bare/skills/e2e-bare-skill/SKILL.md": skill("e2e-bare-skill", "A skill of a plugin with no manifest"),
  },
  "claude-market",
);

const server = await serveGit(served, PORT);
const alan = mkdtempSync(join(tmpdir(), "gen9-plugins-alan-"));
const ada = mkdtempSync(join(tmpdir(), "gen9-plugins-ada-"));
// The chats it makes, by whose they are: deleted at the end whatever failed, through the API with
// that person's terminal token (a failed step once left two: manual-e2e.md, P5-Z1)
const made = [];
const browser = await launch({ headless: !process.env.HEADED, defaultViewport: { width: 1280, height: 900 } });
const page = await browser.newPage();
const q = (s) => s.replaceAll("'", "''");
const sourceByUrl = (url) => JSON.parse(psql(`select coalesce(row_to_json(s)::text, 'null') from plugin_sources s where url = '${q(url)}'`) || "null");
const pluginsOf = (sourceId) =>
  Object.fromEntries(
    JSON.parse(psql(`select coalesce(json_agg(json_build_object('id', id, 'name', name, 'status', status, 'reason', reason, 'format', format, 'availability', availability, 'report', report, 'synced_at', synced_at))::text, '[]') from plugins where source_id = '${sourceId}'`)).map((p) => [p.name, p]),
  );
const skillsOf = (plugin) => (plugin?.report?.skills ?? []).map((s) => s.name).sort().join();
async function untilSynced(url, after = null) {
  for (let i = 0; i < 120; i++) {
    const source = sourceByUrl(url);
    if (source && source.status !== "pending" && source.synced_at !== after) return source;
    await sleep(1000);
  }
  throw new Error(`${url} didn't sync in 2 minutes`);
}
const openAdmin = () => page.goto(`${APP}/admin/plugins`, { waitUntil: "networkidle0" });
const text = (selector) => page.$eval(selector, (e) => e.textContent.replace(/\s+/g, " ").trim()).catch(() => "");
// Add a source in the form; what the form says when it refuses, or "" once it's listed
async function add(url) {
  await openAdmin();
  const field = 'form[aria-label="Add a source"] input[type="url"]';
  await page.click(field, { count: 3 });
  await page.type(field, url);
  await page.click('form[aria-label="Add a source"] button[type="submit"]');
  await page.waitForFunction(
    (u) => document.querySelector("#add-source-problem") || [...document.querySelectorAll('ul[aria-label="Sources"] li')].some((li) => li.textContent.includes(u)),
    { timeout: 30_000 },
    url,
  );
  return text("#add-source-problem");
}
async function menu(name, item) {
  await openAdmin();
  await page.click(`button[aria-label="Actions for ${name}"]`);
  await page.waitForSelector("[role=menuitem]");
  for (const el of await page.$$("[role=menuitem]")) if ((await el.evaluate((e) => e.textContent.trim())) === item) return el.click();
  throw new Error(`no ${item} in the menu for ${name}`);
}

const MARKET_URL = `${GIT}/market.git`;
const added = [];
try {
  await page.goto(`${APP}/auth/login?returnTo=/admin/plugins`, { waitUntil: "networkidle0" });
  await page.type("#username", env.GEN9_SEED_ADMIN_EMAIL);
  await page.type("#password", env.GEN9_SEED_ADMIN_PASSWORD);
  await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }), page.click("#kc-login")]);
  await secondStep(page); // admins need a second step: the seeded admin's code
  check((await text("h1")) === "Plugins", "the seeded admin opens Admin > Plugins", await text("h1"));
  check(Boolean(await signInTerminal({ email: env.GEN9_SEED_USER_EMAIL, password: env.GEN9_SEED_USER_PASSWORD, configDir: alan })), "the seeded user signs in on the terminal");
  await signInTerminal({ email: env.GEN9_SEED_ADMIN_EMAIL, password: env.GEN9_SEED_ADMIN_PASSWORD, configDir: ada });

  // 1. A marketplace in Codex's format
  const refusedFirst = await add(MARKET_URL);
  added.push(MARKET_URL);
  check(refusedFirst === "", "adding the repository lists it", refusedFirst);
  const market = await untilSynced(MARKET_URL);
  check(market.status === "synced" && market.format === "codex" && market.name === "e2e-market", "it syncs: a Codex marketplace", `${market.status} ${market.format} ${market.name} ${market.error ?? ""}`);
  await openAdmin();
  const sourceLine = await text('ul[aria-label="Sources"]');
  check(/e2e-market/.test(sourceLine) && /Synced .* 6 plugins/.test(sourceLine), "its row says when it synced and how many plugins it lists", sourceLine.slice(0, 160));
  let found = pluginsOf(market.id);
  const portable = found["e2e-portable"];
  check(portable?.status === "loaded" && portable.format === "agent-plugins" && skillsOf(portable) === "e2e-portable-greeting", "the Agent Plugins plugin loads with its skill", `${portable?.status} ${portable?.format} ${skillsOf(portable)}`);
  const servers = Object.fromEntries((portable?.report?.mcp_servers ?? []).map((s) => [s.name, s.connects]));
  check(servers.docs === true && servers.local === false, "its remote MCP server would connect, its stdio one never runs", JSON.stringify(servers));
  const files = portable ? psql(`select string_agg(path, ',') from plugin_files where plugin_id = '${portable.id}'`).split(",").sort().join() : "";
  check(files === "skills/e2e-portable-greeting/SKILL.md,skills/e2e-portable-greeting/references/words.md", "the files of its skill are kept, and nothing else of the plugin", files);
  const claude = found["e2e-claude"];
  const claudeNotes = (claude?.report?.notes ?? []).join(" | ");
  check(
    claude?.status === "loaded" && claude.format === "claude" && skillsOf(claude) === "e2e-claude-notes" && /not its folder's/.test(claudeNotes) && /commands/.test(claudeNotes),
    "the Claude Code plugin loads; its skill named unlike its folder is kept with a note, its commands noted as unused",
    `${claude?.status} ${claude?.format} ${claudeNotes}`,
  );
  check(found["e2e-broken"]?.status === "rejected" && /^its name 'Broken_Name' is not lowercase/.test(found["e2e-broken"]?.reason ?? ""), "the broken plugin is rejected, saying why in words", found["e2e-broken"]?.reason);
  check(found["e2e-extra"]?.status === "loaded" && skillsOf(found["e2e-extra"]) === "e2e-extra-skill", "the plugin in another repository is fetched and loads", `${found["e2e-extra"]?.status} ${found["e2e-extra"]?.reason ?? ""}`);
  check(found["e2e-sub"]?.status === "loaded" && skillsOf(found["e2e-sub"]) === "e2e-sub-skill", "the plugin in a monorepo's folder is fetched sparsely and loads", `${found["e2e-sub"]?.status} ${found["e2e-sub"]?.reason ?? ""}`);
  check(found["e2e-npm"]?.status === "unsupported" && /npm/.test(found["e2e-npm"]?.reason ?? ""), "the npm one is listed as unsupported, saying why", found["e2e-npm"]?.reason);
  const listed = await text('ul[aria-label="Plugins from e2e-market"]');
  check(/2 skills|1 skill · 1 connector · 1 server not run/.test(listed) && /Couldn’t load: /.test(listed) && /Not supported: /.test(listed) && /Claude Code/.test(listed), "the screen lists what each brings, and why the others don't load", listed.slice(0, 200));

  // 2. Availability
  check(Object.values(found).every((p) => p.availability === "off"), "every new plugin starts off");
  const select = `#availability-${portable.id}`;
  check(!(await page.$(`#availability-${found["e2e-broken"].id}`)), "a plugin that didn't load has no availability to choose");
  await page.select(select, "available");
  let chosen = "";
  for (let i = 0; i < 20 && chosen !== "available"; i++) {
    await sleep(500);
    chosen = psql(`select availability from plugins where id = '${portable.id}'`);
  }
  check(chosen === "available", "choosing “People who add it” saves at once", chosen);

  // 3. A change, then Sync now
  const extraSynced = found["e2e-extra"].synced_at;
  const moved = publish(
    "market",
    {
      ".agents/plugins/marketplace.json": { ...MARKET, plugins: MARKET.plugins.filter((p) => p.name !== "e2e-npm") },
      "plugins/portable/skills/e2e-portable-farewell/SKILL.md": skill("e2e-portable-farewell", "Says goodbye in the plugin's words"),
    },
    "a second skill; no npm plugin",
  );
  await menu("e2e-market", "Sync now");
  await page.waitForFunction(() => document.querySelector('ul[aria-label="Sources"]')?.textContent.includes("Syncing"), { timeout: 10_000 }).catch(() => {});
  const saidSyncing = (await text('ul[aria-label="Sources"]')).includes("Syncing");
  const resynced = await untilSynced(MARKET_URL, market.synced_at);
  found = pluginsOf(market.id);
  check(resynced.commit === moved, "Sync now fetches the new commit", `said Syncing: ${saidSyncing}; ${resynced.commit?.slice(0, 7)} ${moved.slice(0, 7)}`);
  await page.waitForFunction(() => /Synced/.test(document.querySelector('ul[aria-label="Sources"]')?.textContent ?? ""), { timeout: 15_000 }).catch(() => {});
  check(skillsOf(found["e2e-portable"]) === "e2e-portable-farewell,e2e-portable-greeting", "the pushed skill is kept", skillsOf(found["e2e-portable"]));
  check(found["e2e-portable"]?.availability === "available", "the admin's choice outlives the sync");
  // Changed while available: nobody gets it until the admin looks (P5-C4)
  const CHANGED = `[role="group"][aria-label="e2e-portable changed"]`;
  const agreed = () => psql(`select reviewed is not distinct from encode(sha256(convert_to(concat(coalesce(digest, ''), report::text), 'UTF8')), 'hex') from plugins where id = '${portable.id}'`);
  await openAdmin();
  const notice = await text(CHANGED);
  const commitNow = psql(`select commit from plugins where id = '${portable.id}'`);
  check(agreed() === "f" && notice.includes("It changed since you chose who can use it") && notice.includes(commitNow.slice(0, 7)), "the changed plugin reaches nobody until the admin looks: its row says so, with its commit", notice.slice(0, 140));
  // What its new skill says, read in Details before letting people have it
  const row = await page.$(`${CHANGED}`).then((g) => g.evaluateHandle((e) => e.closest("li")));
  await row.evaluate((li) => li.querySelector(":scope > details").setAttribute("open", ""));
  const file = "skills/e2e-portable-farewell/SKILL.md";
  await row.evaluate((li, f) => [...li.querySelectorAll("details details")].find((d) => d.textContent.includes(f))?.querySelector("summary")?.click(), file);
  const read = await page.waitForFunction((f) => [...document.querySelectorAll("details details[open] pre")].map((p) => p.textContent).find((t) => t.includes("e2e-portable-farewell")) ?? false, { timeout: 15_000 }, file).then((h) => h.jsonValue(), () => "");
  check(/name: e2e-portable-farewell/.test(read) && /Follow these steps\./.test(read), "the admin reads what the new skill says, in its Details", read.replace(/\s+/g, " ").slice(0, 100));
  for (const b of await page.$$(`${CHANGED} button`)) if ((await b.evaluate((e) => e.textContent.trim())) === "Let people have it again") await b.click();
  for (let i = 0; i < 20 && agreed() !== "t"; i++) await sleep(500);
  await openAdmin();
  check(agreed() === "t" && !(await page.$(CHANGED)), "“Let people have it again” agrees to it as it is now", agreed());
  check(!found["e2e-npm"] && Object.keys(found).length === 5, "the plugin taken out of the marketplace goes", Object.keys(found).join());
  check(found["e2e-extra"]?.synced_at === extraSynced, "the plugin in another repository isn't fetched again: its commit didn't move");

  // 3b. The repository goes away, then Sync now: the sync fails, and the row still says when it
  // last synced and how many plugins it still offers (P3-D5); back, it syncs again
  const lastGood = sourceByUrl(MARKET_URL);
  renameSync(join(served, "market.git"), join(served, "market-away.git"));
  await menu("e2e-market", "Sync now");
  const away = await untilSynced(MARKET_URL, lastGood.synced_at);
  await openAdmin();
  await page.waitForFunction(() => /Couldn’t sync/.test(document.querySelector('ul[aria-label="Sources"]')?.textContent ?? ""), { timeout: 15_000 }).catch(() => {});
  const awayRow = await text('ul[aria-label="Sources"]');
  check(
    away.status === "failed" && away.succeeded_at === lastGood.succeeded_at && Object.keys(pluginsOf(market.id)).length === 5 && /Couldn’t sync: .*Last synced .+ · 5 plugins/.test(awayRow),
    "a source whose repository went away says it couldn't sync, and still when it last did and what it offers",
    awayRow.match(/Couldn’t sync: .*?5 plugins/)?.[0]?.slice(0, 200) ?? awayRow.slice(0, 200),
  );
  renameSync(join(served, "market-away.git"), join(served, "market.git"));
  await menu("e2e-market", "Sync now");
  const back = await untilSynced(MARKET_URL, away.synced_at);
  check(back.status === "synced" && back.succeeded_at > lastGood.succeeded_at, "back, it syncs again", `${back.status}; ${back.error ?? "no error"}`);

  // 4. Claude Code's format
  const CC_URL = `${GIT}/claude-market.git`;
  await add(CC_URL);
  added.push(CC_URL);
  const cc = await untilSynced(CC_URL);
  const bare = pluginsOf(cc.id)["e2e-bare"];
  check(cc.format === "claude" && bare?.status === "loaded" && bare.format === "claude" && skillsOf(bare) === "e2e-bare-skill", "a Claude Code marketplace syncs, its entry standing in for a missing manifest", `${cc.format} ${bare?.status} ${bare?.format}`);

  // 5. Refused
  const plain = await add("http://example.com/plugins.git");
  check(/https/.test(plain), "plain http to a host nobody named is refused, in the form", plain);
  const privateNet = await add("https://10.0.0.1/plugins.git");
  check(/private network/.test(privateNet), "a private address is refused, in the form", privateNet);
  const REDIRECT_URL = `${GIT}/redirect/market.git`;
  await add(REDIRECT_URL);
  added.push(REDIRECT_URL);
  const redirected = await untilSynced(REDIRECT_URL);
  await openAdmin();
  const redirectRow = await text('ul[aria-label="Sources"]');
  check(redirected.status === "failed" && /301/.test(redirected.error ?? "") && /Couldn’t sync: git failed/.test(redirectRow), "a redirect is not followed: the source says it couldn't sync, and why", redirected.error);
  const EMPTY_URL = `${GIT}/extra.git`;
  await add(EMPTY_URL);
  added.push(EMPTY_URL);
  const empty = await untilSynced(EMPTY_URL);
  check(empty.status === "failed" && /marketplace\.json/.test(empty.error ?? ""), "a repository without a marketplace fails, saying where Gen9 looked", empty.error);

  // 6. A person's plugins
  const turn = async (p, text) => {
    await p.type("#composer", text);
    await p.keyboard.press("Enter");
    await p.waitForSelector('button[aria-label="Stop"]', { timeout: 30_000 }).catch(() => {});
    await p.waitForFunction(() => !document.querySelector('button[aria-label="Stop"]'), { timeout: 300_000, polling: 500 });
    return p.$$eval("ol[aria-live] > li", (lis) => lis.at(-1)?.textContent ?? "");
  };
  // Reads of a plugin's skill that found it (a read of a path no longer there fails)
  const readsOfPlugins = (thread) =>
    psql(
      `select count(*) from run_events s join runs r on r.id = s.run_id join run_events c on c.run_id = s.run_id and c.type = 'tool.completed' and c.data->>'id' = s.data->>'id'` +
        ` where r.thread_id = '${thread}' and s.type = 'tool.started' and s.data->'args'->>'file_path' like '/plugins/%' and c.data->>'status' = 'success' and coalesce(c.data->>'output', '') not ilike '%not found%'`,
    );
  // Delete a chat the way a person does, so its checkpoints and traces go too
  async function deleteChat(p, id) {
    await p.goto(`${APP}/chat/${id}`, { waitUntil: "networkidle0" });
    await p.click('button[aria-label="Chat options"]');
    // By its name: the menu has other items (Rename comes first)
    await p.waitForSelector("[role=menuitem]", { timeout: 5000 });
    let item = null;
    for (const el of await p.$$("[role=menuitem]")) if ((await el.evaluate((e) => e.textContent.trim())) === "Delete chat") item = el;
    if (!item) throw new Error(`no Delete chat in the menu of chat ${id}`);
    await item.click();
    const confirm = await p.waitForSelector('[role="alertdialog"] button:last-of-type', { timeout: 5000 });
    await Promise.all([p.waitForNavigation({ waitUntil: "networkidle0" }).catch(() => {}), confirm.click()]);
  }
  const alanBrowser = await browser.createBrowserContext();
  const alanPage = await alanBrowser.newPage();
  await alanPage.goto(`${APP}/auth/login?returnTo=/settings`, { waitUntil: "networkidle0" });
  await alanPage.type("#username", env.GEN9_SEED_USER_EMAIL);
  await alanPage.type("#password", env.GEN9_SEED_USER_PASSWORD);
  await Promise.all([alanPage.waitForNavigation({ waitUntil: "networkidle0" }), alanPage.click("#kc-login")]);
  const offered = await alanPage.$eval('ul[aria-label="Plugins"]', (u) => u.textContent).catch(() => "");
  check(/e2e-portable/.test(offered) && /2 skills · 1 connector/.test(offered), "Settings offers the seeded user the available plugin, with what it brings", offered.slice(0, 120));
  await alanPage.click('button[aria-label="Add e2e-portable"]');
  await alanPage.waitForSelector('button[aria-label="Remove e2e-portable"]', { timeout: 15_000 });
  const skillsListed = await alanPage.$eval('ul[aria-label="Skills"]', (u) => u.textContent).catch(() => "");
  check(/e2e-portable-greeting · From e2e-portable/.test(skillsListed) && /research-brief · Gen9’s own/.test(skillsListed), "added, its skills are listed with where they come from", skillsListed.slice(0, 160));
  // Its remote server, as the seeded user's connector
  const connectorsOf = (email) =>
    psql(`select coalesce(string_agg(c.name || ':' || c.status || ':' || jsonb_array_length(c.tools), ','), '') from connectors c join users u on u.id = c.user_id where u.email = '${q(email)}' and c.plugin_id = '${portable.id}'`);
  check(connectorsOf(env.GEN9_SEED_USER_EMAIL) === "docs:ready:3", "the plugin's remote MCP server became their connector, ready with its tools", connectorsOf(env.GEN9_SEED_USER_EMAIL));
  await alanPage.goto(`${APP}/settings`, { waitUntil: "networkidle0" });
  const docsRow = await alanPage.evaluate(() => {
    const select = document.querySelector('select[aria-label="When Gen9 asks before using docs"]');
    const row = select?.closest("div.grid");
    return row ? { text: row.textContent, buttons: [...row.querySelectorAll("button")].map((b) => b.textContent.trim()) } : null;
  });
  check(/docs · From e2e-portable/.test(docsRow?.text ?? "") && !docsRow.buttons.includes("Remove"), "Settings lists it as from the plugin, with no Remove of its own", `${docsRow?.text?.slice(0, 80)}; buttons: ${docsRow?.buttons}`);
  await alanPage.goto(`${APP}/chat`, { waitUntil: "networkidle0" });
  await alanPage.type("#composer", "Use your docs connector's read_wiki_structure tool for the repository langchain-ai/deepagents, then reply with the names of 2 of its documentation topics, comma-separated, nothing else.");
  await alanPage.keyboard.press("Enter");
  const CARD = 'section[aria-label="Gen9 needs your approval"]';
  await alanPage.waitForSelector(CARD, { timeout: 180_000 });
  const docsChat = alanPage.url().match(/\/chat\/([0-9a-f-]{36})/)?.[1];
  made.push([alan, docsChat]);
  const card = await alanPage.$eval(CARD, (c) => c.textContent);
  check(card.includes("Gen9 wants to use docs: read wiki structure"), "its call waits for Allow, like any connector's", card.slice(0, 90));
  for (const b of await alanPage.$$(`${CARD} button`)) if ((await b.evaluate((e) => e.textContent.trim())) === "Allow") await b.click();
  await alanPage.waitForFunction(() => !document.querySelector('button[aria-label="Stop"]') && !document.querySelector('section[aria-label="Gen9 needs your approval"]'), { timeout: 300_000, polling: 500 });
  await alanPage.reload({ waitUntil: "networkidle0" });
  await alanPage.click("ol[aria-live] > li:last-child details > summary").catch(() => {});
  const docsSteps = await alanPage.$$eval('ul[aria-label="Tools used"] > li', (lis) => lis.map((li) => li.textContent.trim()));
  check(docsSteps.some((t) => t.startsWith("Used docs: read wiki structure")), "allowed, it runs and the step names it", docsSteps.join(" | "));
  if (docsChat) await deleteChat(alanPage, docsChat);

  await alanPage.goto(`${APP}/chat`, { waitUntil: "networkidle0" });
  const greeted = await turn(alanPage, ASK);
  const alanChat = alanPage.url().match(/\/chat\/([0-9a-f-]{36})/)?.[1];
  made.push([alan, alanChat]);
  check(greeted.includes(PHRASE) && /Used the e2e portable greeting skill from e2e-portable/.test(greeted), "the seeded user's chat follows the plugin's skill, and the step names the plugin", greeted.slice(0, 200));
  await alanPage.reload({ waitUntil: "networkidle0" });
  const history = await alanPage.$$eval("ol[aria-live] > li", (lis) => lis.map((li) => li.textContent).join(" | "));
  check(/Used the e2e portable greeting skill from e2e-portable/.test(history), "after a reload, the step still names the plugin", history.slice(0, 200));
  await page.goto(`${APP}/chat`, { waitUntil: "networkidle0" });
  const notAda = await turn(page, ASK);
  const adaChat = page.url().match(/\/chat\/([0-9a-f-]{36})/)?.[1];
  made.push([ada, adaChat]);
  check(!notAda.includes(PHRASE) && readsOfPlugins(adaChat) === "0", "the admin didn't add it: their chat has no such skill", notAda.slice(0, 120));
  check(connectorsOf(env.GEN9_SEED_ADMIN_EMAIL) === "", "nor its connector", connectorsOf(env.GEN9_SEED_ADMIN_EMAIL));
  await openAdmin();
  await page.select(`#availability-${portable.id}`, "installed");
  for (let i = 0; i < 20 && psql(`select availability from plugins where id = '${portable.id}'`) !== "installed"; i++) await sleep(500);
  await page.goto(`${APP}/settings`, { waitUntil: "networkidle0" });
  const adaPlugins = await page.$eval('ul[aria-label="Plugins"]', (u) => u.textContent).catch(() => "");
  const adaSkills = await page.$eval('ul[aria-label="Skills"]', (u) => u.textContent).catch(() => "");
  check(/Everyone has it/.test(adaPlugins) && /e2e-portable-greeting · From e2e-portable/.test(adaSkills), "made “Everyone”, the admin has it too, and can't remove it", adaPlugins.slice(0, 120));
  // A change synced while everyone has it waits for the admin, then comes back (P5-C4)
  const syncedBefore = sourceByUrl(MARKET_URL).synced_at;
  publish("market", { "plugins/portable/skills/e2e-portable-greeting/references/words.md": "Hello again from the portable plugin.\n" }, "reworded");
  await menu("e2e-market", "Sync now");
  await untilSynced(MARKET_URL, syncedBefore);
  await page.goto(`${APP}/settings`, { waitUntil: "networkidle0" });
  const waitingPlugins = await page.$eval('ul[aria-label="Plugins"]', (u) => u.textContent).catch(() => "");
  const waitingSkills = await page.$eval('ul[aria-label="Skills"]', (u) => u.textContent).catch(() => "");
  check(/an admin hasn’t looked at it yet/.test(waitingPlugins) && !/e2e-portable-greeting/.test(waitingSkills), "changed while everyone has it: Settings says it waits for an admin, and its skill is off", waitingPlugins.slice(0, 160));
  // Looked at in one tab, changed again before the admin says yes there: refused
  const stale = await browser.newPage();
  await stale.goto(`${APP}/admin/plugins`, { waitUntil: "networkidle0" });
  const syncedAgain = sourceByUrl(MARKET_URL).synced_at;
  publish("market", { "plugins/portable/skills/e2e-portable-greeting/references/words.md": "Hello once more from the portable plugin.\n" }, "reworded again");
  // A tab in the background gets no animation frames, which Puppeteer's waits use
  await page.bringToFront();
  await menu("e2e-market", "Sync now");
  await untilSynced(MARKET_URL, syncedAgain);
  await stale.bringToFront();
  for (const b of await stale.$$(`${CHANGED} button`)) if ((await b.evaluate((e) => e.textContent.trim())) === "Let people have it again") await b.click();
  const refused = await stale.waitForFunction(() => document.body.innerText.includes("It changed since you looked"), { timeout: 15_000 }).then(() => true, () => false);
  check(refused && agreed() === "f", "a change made after the admin looked isn't agreed to: “It changed since you looked”", `told: ${refused}; agreed: ${agreed()}`);
  await stale.close();
  await page.bringToFront();
  await openAdmin();
  for (const b of await page.$$(`${CHANGED} button`)) if ((await b.evaluate((e) => e.textContent.trim())) === "Let people have it again") await b.click();
  await page.waitForFunction((sel) => !document.querySelector(sel), { timeout: 15_000 }, CHANGED).catch(() => {});
  await page.goto(`${APP}/settings`, { waitUntil: "networkidle0" });
  const backSkills = await page.$eval('ul[aria-label="Skills"]', (u) => u.textContent).catch(() => "");
  check(/e2e-portable-greeting · From e2e-portable/.test(backSkills), "let have again, its skill is back", backSkills.slice(0, 120));
  await openAdmin();
  await page.select(`#availability-${portable.id}`, "available");
  for (let i = 0; i < 20 && psql(`select availability from plugins where id = '${portable.id}'`) !== "available"; i++) await sleep(500);
  await alanPage.goto(`${APP}/settings`, { waitUntil: "networkidle0" });
  await alanPage.click('button[aria-label="Remove e2e-portable"]');
  await alanPage.waitForSelector('button[aria-label="Add e2e-portable"]', { timeout: 15_000 });
  // Measured on a settled page: a button changing style mid-transition reads as low contrast
  await alanPage.reload({ waitUntil: "networkidle0" });
  await alanPage.evaluate(AXE);
  const settingsAxe = await alanPage.evaluate(() => window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] } }));
  const settingsBlocking = settingsAxe.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  check(
    !settingsBlocking.length,
    "Settings with plugins and skills has no serious accessibility violations",
    settingsBlocking.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`).join("; "),
  );
  await alanPage.goto(`${APP}/chat/${alanChat}`, { waitUntil: "networkidle0" });
  const before = readsOfPlugins(alanChat);
  const after = await turn(alanPage, `${ASK} Use the same phrase as before only if the skill is still there.`);
  check(readsOfPlugins(alanChat) === before, "removed, the next message of the same chat can't read the plugin's skill", `${after.slice(0, 120)}; skill reads ${before} → ${readsOfPlugins(alanChat)}`);
  check(connectorsOf(env.GEN9_SEED_USER_EMAIL) === "", "and its connector is gone", connectorsOf(env.GEN9_SEED_USER_EMAIL));
  if (alanChat) await deleteChat(alanPage, alanChat);
  if (adaChat) await deleteChat(page, adaChat);
  await alanBrowser.close();

  // 7. Accessibility, and admins only
  await openAdmin();
  await page.evaluate(AXE);
  const { violations } = await page.evaluate(() => window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] } }));
  const blocking = violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  check(!blocking.length, "Admin > Plugins has no serious accessibility violations", blocking.map((v) => v.id).join(", "));
  const asUser = await api(alan, "GET", "/v1/admin/plugin-sources");
  const addAsUser = await api(alan, "POST", "/v1/admin/plugin-sources", { url: MARKET_URL });
  check(asUser.status === 403 && addAsUser.status === 403, "the seeded user can't list or add sources", `${asUser.status} ${addAsUser.status}`);

  // 8. Removal
  const kept = psql(`select count(*) from plugin_files f join plugins p on p.id = f.plugin_id where p.source_id = '${market.id}'`);
  await menu("e2e-market", "Remove…");
  const dialog = await page.waitForSelector('[role="alertdialog"]');
  const asked = await dialog.evaluate((d) => d.textContent);
  for (const b of await page.$$('[role="alertdialog"] button')) if ((await b.evaluate((e) => e.textContent.trim())) === "Remove") await b.click();
  let left = "";
  for (let i = 0; i < 20 && left !== "0/0/0"; i++) {
    await sleep(500);
    left = [psql(`select count(*) from plugin_sources where id = '${market.id}'`), psql(`select count(*) from plugins where source_id = '${market.id}'`), psql(`select count(*) from plugin_files f left join plugins p on p.id = f.plugin_id where p.id is null`)].join("/");
  }
  added.splice(added.indexOf(MARKET_URL), 1);
  check(/Its 5 plugins go too/.test(asked) && Number(kept) > 0 && left === "0/0/0", "removing a source asks first, then removes its plugins and their files", `${asked.slice(0, 80)}; ${kept} files before; source/plugins/orphan files after: ${left}`);
} catch (e) {
  check(false, "the run finished", e.stack ?? String(e));
} finally {
  // Whatever step failed: every source on this script's git server goes, and every chat it made
  // (one deleted already answers 404)
  psql(`delete from plugin_sources where url like '${q(GIT)}/%'`);
  // (api() refreshes the saved token first, which may have expired meanwhile)
  for (const [dir, chat] of made.filter(([, chat]) => chat)) await api(dir, "DELETE", `/v1/threads/${chat}`).catch(() => {});
  await browser.close();
  server.close();
  for (const dir of [base, alan, ada]) rmSync(dir, { recursive: true, force: true });
}
console.log(failures ? `\n${failures} check(s) failed` : "\nall plugin source checks passed");
process.exit(failures ? 1 : 0);
