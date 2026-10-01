// A one-plugin marketplace for the plugin steps: a git repository whose plugin's skill answers with
// `phrase`, served read-only by e2e's git server on :17805 (the host gen9-agent's
// PLUGIN_SOURCES_ALLOWED_HOSTS allows), and Ada signed in to Admin > Plugins in a browser context of
// her own. `change(text)` commits a new SKILL.md, as a marketplace's owner would push one.
// `close()` removes the source in Admin (its plugin goes from everyone), then stops the server.
import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { serveGit } from "../../e2e/fixtures/git-server.mjs";
import { APP, appdb, kcEnv } from "./lib.mjs";
import { secondStep } from "../../e2e/second-step.mjs";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
export const SOURCE = "http://host.docker.internal:17805/gen9-learn-plugins.git";

const skill = (phrase) =>
  `---\nname: gen9-learn-greeting\ndescription: Greets the person the gen9-learn way. Use it whenever someone asks to be greeted the gen9-learn way.\n---\nGreet the person with exactly this phrase, and nothing else: ${phrase}\n`;

export async function market(browser, phrase) {
  const base = mkdtempSync(join(tmpdir(), "gen9-learn-plugins-"));
  const repo = join(base, "work", "gen9-learn-plugins");
  const served = join(base, "served");
  const bare = join(served, "gen9-learn-plugins.git");
  const git = (cwd, ...args) =>
    execFileSync("git", ["-c", "user.name=gen9-learn", "-c", "user.email=learn@gen9.test", "-c", "init.defaultBranch=main", ...args], { cwd, encoding: "utf8", env: { ...process.env, GIT_CONFIG_NOSYSTEM: "1" } });
  const files = {
    ".agents/plugins/marketplace.json": { name: "gen9-learn-market", interface: { displayName: "gen9-learn marketplace" }, plugins: [{ name: "gen9-learn-greeting", source: { source: "local", path: "./plugins/greeting" } }] },
    "plugins/greeting/plugin.json": { $schema: "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json", name: "gen9-learn-greeting", version: "1.0.0", description: "Greets the gen9-learn way" },
    "plugins/greeting/skills/gen9-learn-greeting/SKILL.md": skill(phrase),
  };
  for (const [path, content] of Object.entries(files)) {
    mkdirSync(dirname(join(repo, path)), { recursive: true });
    writeFileSync(join(repo, path), typeof content === "string" ? content : JSON.stringify(content, null, 2));
  }
  mkdirSync(served, { recursive: true });
  git(repo, "init", "-q");
  git(repo, "add", "-A");
  git(repo, "commit", "-qm", "A marketplace with one plugin");
  git(base, "clone", "-q", "--bare", repo, bare);
  const server = await serveGit(served, 17805);

  const adminContext = await browser.createBrowserContext();
  const ada = await adminContext.newPage();
  await ada.goto(`${APP}/auth/login?returnTo=/admin/plugins`, { waitUntil: "networkidle0" });
  await ada.type("#username", kcEnv.GEN9_SEED_ADMIN_EMAIL);
  await ada.type("#password", kcEnv.GEN9_SEED_ADMIN_PASSWORD);
  await Promise.all([ada.waitForNavigation({ waitUntil: "networkidle0" }), ada.click("#kc-login")]);
  await secondStep(ada); // admins need a second step: the seeded admin's code
  if (!ada.url().endsWith("/admin/plugins")) await ada.goto(`${APP}/admin/plugins`, { waitUntil: "networkidle0" });

  const menu = async (item) => {
    await ada.goto(`${APP}/admin/plugins`, { waitUntil: "networkidle0" });
    await ada.click('button[aria-label="Actions for gen9-learn-market"]');
    await ada.waitForSelector("[role=menuitem]");
    for (const el of await ada.$$("[role=menuitem]")) if ((await el.evaluate((e) => e.textContent.trim())) === item) return el.click();
    throw new Error(`no ${item} in the source's menu`);
  };
  const plugin = () =>
    appdb(`select p.id || '|' || p.status || '|' || p.availability from plugins p join plugin_sources s on s.id = p.source_id where s.url = '${SOURCE}' and p.name = 'gen9-learn-greeting'`);

  return {
    ada,
    menu,
    plugin,
    /** Ada adds the source; resolves with the plugin's id once it's loaded. */
    async add() {
      await ada.type('form[aria-label="Add a source"] input[type="url"]', SOURCE);
      await ada.click('form[aria-label="Add a source"] button[type="submit"]');
      let found = "";
      for (let i = 0; i < 60 && !found.includes("|loaded|"); i++) {
        await sleep(1000);
        found = plugin();
      }
      return found.split("|")[0];
    },
    /** Ada chooses who may have it: off, available or installed. */
    async choose(pluginId, availability) {
      await ada.goto(`${APP}/admin/plugins`, { waitUntil: "networkidle0" });
      await ada.select(`#availability-${pluginId}`, availability);
      for (let i = 0; i < 20 && appdb(`select availability from plugins where id = '${pluginId}'`) !== availability; i++) await sleep(500);
      return appdb(`select availability from plugins where id = '${pluginId}'`);
    },
    /** A new commit with the skill answering `next`, pushed to the served repository. */
    change(next) {
      writeFileSync(join(repo, "plugins/greeting/skills/gen9-learn-greeting/SKILL.md"), skill(next));
      git(repo, "commit", "-qam", "The greeting changes");
      git(repo, "push", "-q", bare, "HEAD:main");
      return git(repo, "rev-parse", "HEAD").trim();
    },
    async close() {
      try {
        await menu("Remove…");
        await ada.waitForSelector('[role="alertdialog"]');
        for (const b of await ada.$$('[role="alertdialog"] button')) if ((await b.evaluate((e) => e.textContent.trim())) === "Remove") await b.click();
        for (let i = 0; i < 20 && appdb(`select count(*) from plugin_sources where url = '${SOURCE}'`) !== "0"; i++) await sleep(500);
      } finally {
        await adminContext.close();
        server.close();
        rmSync(base, { recursive: true, force: true });
      }
      return appdb(`select count(*) from plugin_sources where url = '${SOURCE}'`);
    },
  };
}
