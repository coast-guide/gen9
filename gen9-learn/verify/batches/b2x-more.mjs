// Batch 2x, giving it more: a researched answer (the built-in research-brief skill, web search
// through the router, the answer's Sources), a connector (DeepWiki's public MCP server, added in
// Settings, its call waiting for Allow), an environment (a command in the chat's own sandbox, a
// file it shares under the answer) and a plugin (a marketplace served here by e2e's git server,
// added by Ada in Admin > Plugins, then by the person; its skill steers the chat). What ran is
// read from the run's events in Postgres, not from the model's retelling.
import { randomBytes } from "node:crypto";
import { APP, appdb, check, sh } from "../lib.mjs";
import { SOURCE, market } from "../market.mjs";

const SOURCES = 'ol[aria-live] > li:last-child button[aria-label^="Sources"]';
const CARD = 'section[aria-label="Gen9 needs your approval"]';
const SKILL = "/skills/research-brief/SKILL.md";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const modelsdb = (q) => sh(`docker exec gen9-models-postgres-1 psql -U litellm -d litellm -tAc "${q.replaceAll('"', '\\"')}"`).out;
// A chat's tool steps, in order: each tool's name and, for a file, its path
const stepsOf = (thread) =>
  appdb(
    `select coalesce(string_agg(e.data->>'name' || coalesce(' ' || (e.data->'args'->>'file_path'), ''), '; ' order by e.seq), '')` +
      ` from run_events e join runs r on r.id = e.run_id where r.thread_id = '${thread}' and e.type = 'tool.started'`,
  );
// What the chat's commands printed (execute's outputs)
const printed = (thread) =>
  appdb(
    `select coalesce(string_agg(e.data->>'output', ' | ' order by e.seq), '') from run_events e join runs r on r.id = e.run_id` +
      ` where r.thread_id = '${thread}' and e.type = 'tool.completed' and e.data->>'name' = 'execute'`,
  );
const containersOf = (thread) => sh(`docker ps --filter label=gen9-thread=${thread} --format '{{.Names}}'`).out.split("\n").filter(Boolean);

export default async function more(ctx) {
  const { page, rec, user } = ctx;
  const obs = {};
  const button = async (scope, text) => {
    for (const b of await page.$$(`${scope} button`)) if ((await b.evaluate((e) => e.textContent.trim())) === text) return b;
    throw new Error(`no ${text} button in ${scope}`);
  };
  // Sends a message and waits for the turn to end (or to wait for the person)
  const send = async (text, { card = false } = {}) => {
    await page.type("#composer", text);
    await page.keyboard.press("Enter");
    await page.waitForSelector('button[aria-label="Stop"]', { timeout: 30_000 }).catch(() => {});
    await page.waitForFunction(
      (c) => !document.querySelector('button[aria-label="Stop"]') || (c && document.querySelector(c)),
      { timeout: 300_000, polling: 500 },
      card ? CARD : null,
    );
    return page.url().match(/\/chat\/([0-9a-f-]{36})/)?.[1];
  };
  const newChat = async () => page.goto(`${APP}/chat`, { waitUntil: "networkidle0" });

  // 2x.1 A research brief: the skill, the searches, the Sources
  rec.mark("2x.1 research brief");
  await newChat();
  const brief = await send("Write me a short research brief: which PostgreSQL major version first added the SQL/JSON JSON_TABLE function, and when was that version released?");
  obs.briefSteps = stepsOf(brief);
  obs.sourcesButton = await page.$eval(SOURCES, (b) => b.getAttribute("aria-label")).catch(() => null);
  let listed = { links: [] };
  if (obs.sourcesButton) {
    await page.click(SOURCES);
    await page.waitForSelector('[role="dialog"] a[target="_blank"]', { timeout: 10_000 }).catch(() => {});
    listed = await page.$eval('[role="dialog"]', (d) => ({
      heading: d.querySelector("h2")?.textContent,
      links: [...d.querySelectorAll('a[target="_blank"]')].map((a) => new URL(a.href).hostname),
    }));
    await page.keyboard.press("Escape");
  }
  obs.sourceHosts = [...new Set(listed.links)].slice(0, 5);
  // The router writes its spend logs in batches: wait for this user's web searches to appear
  let web = "0";
  for (let i = 0; i < 30 && web === "0"; i++) {
    web = modelsdb(`select count(*) from "LiteLLM_SpendLogs" where end_user = '${user.sub}' and model_group = 'web'`);
    if (web === "0") await sleep(2000);
  }
  obs.webRows = web;
  check(
    obs.briefSteps.includes(`read_file ${SKILL}`) && /web_search/.test(obs.briefSteps) && Boolean(obs.sourcesButton) && listed.links.length > 0 && Number(web) > 0,
    "a research brief: the agent reads the research-brief skill, searches the web through the router (web rows under the user), and the answer lists its Sources",
    `${obs.briefSteps.slice(0, 140)}; ${obs.sourcesButton}; ${obs.sourceHosts.join(", ")}; web rows ${web}`,
  );

  // 2x.2 A connector: DeepWiki's public MCP server, added in Settings
  rec.mark("2x.2 connector");
  await page.goto(`${APP}/settings`, { waitUntil: "networkidle0" });
  await (await button("main", "Add a connector")).click();
  const form = 'form[aria-label="Add a connector"]';
  await page.waitForSelector(form);
  await page.type(`${form} input:not([type=url]):not([type=password])`, "deepwiki");
  await page.type(`${form} input[type=url]`, "https://mcp.deepwiki.com/mcp");
  await (await button(form, "Add")).click();
  await page.waitForFunction(() => [...document.querySelectorAll("main p")].some((p) => p.textContent === "deepwiki"), { timeout: 60_000 });
  obs.connectorRow = appdb(`select c.name || ' | ' || c.url || ' | ' || c.policy || ' | ' || (c.sealed_token is null) from connectors c join users u on u.id = c.user_id where u.sub = '${user.sub}' and c.name = 'deepwiki'`);
  obs.connectorSummary = await page.evaluate(() => {
    const name = [...document.querySelectorAll("main p")].find((p) => p.textContent === "deepwiki");
    return name?.parentElement?.textContent?.replace(/\s+/g, " ").trim().slice(0, 120) ?? "";
  });
  await newChat();
  const wiki = await send("Use your deepwiki connector's read_wiki_structure tool for the repository langchain-ai/deepagents, then reply with the names of 2 of its documentation topics, comma-separated, nothing else.", { card: true });
  obs.approvalCard = await page.$eval(CARD, (c) => c.innerText.replace(/\s+/g, " ")).catch(() => "");
  if (obs.approvalCard) {
    await (await button(CARD, "Allow")).click();
    await page.waitForFunction(() => !document.querySelector('section[aria-label="Gen9 needs your approval"]') && !document.querySelector('button[aria-label="Stop"]'), { timeout: 300_000, polling: 500 });
  }
  await page.click("ol[aria-live] > li:last-child details > summary").catch(() => {});
  obs.connectorSteps = await page.$$eval('ul[aria-label="Tools used"] > li', (lis) => lis.map((li) => li.textContent.trim())).catch(() => []);
  obs.connectorStepsDb = stepsOf(wiki);
  check(
    obs.connectorRow.startsWith("deepwiki | https://mcp.deepwiki.com/mcp | ask | t") && obs.approvalCard.includes("Gen9 wants to use deepwiki") && obs.connectorSteps.some((s) => s.startsWith("Used deepwiki")),
    "a connector: added in Settings (asking every time, no token), its call waits for Allow, then runs as a step naming it",
    `${obs.connectorRow}; ${obs.approvalCard.slice(0, 70)}; ${obs.connectorSteps.find((s) => s.startsWith("Used deepwiki")) ?? obs.connectorStepsDb}`,
  );

  // 2x.3 An environment: a command in the chat's own sandbox, and a file it shares
  rec.mark("2x.3 environment");
  await newChat();
  const env = await send("Run this with python3 in your environment and tell me what it printed: print(6 * 7)");
  obs.envSteps = stepsOf(env);
  obs.envPrinted = printed(env);
  obs.envContainers = containersOf(env).length;
  obs.ranStep = await page.$$eval("ol[aria-live] > li:last-child", (lis) => lis.at(-1)?.textContent.match(/Ran: [^\n]{0,60}/)?.[0] ?? "");
  await send("In your environment, make a CSV with the header name,score and the rows ada,3 and alan,5, and share it with me as scores.csv.");
  await page.waitForSelector('ul[aria-label="Files"] a', { timeout: 30_000 }).catch(() => {});
  const link = await page.$$eval('ul[aria-label="Files"] a', (as) => as.map((a) => ({ href: a.getAttribute("href"), text: a.textContent.trim() })).find((l) => /scores\.csv/.test(l.text))).catch(() => null);
  const got = link
    ? await page.evaluate(async (href) => {
        const r = await fetch(href);
        return { status: r.status, disposition: r.headers.get("content-disposition") ?? "", body: await r.text() };
      }, link.href)
    : { status: 0, disposition: "", body: "" };
  obs.fileLink = link?.text ?? "";
  obs.fileRow = appdb(`select f.name || ' | ' || f.size from chat_files f where f.thread_id = '${env}' and f.name = 'scores.csv'`);
  check(
    /\b42\b/.test(obs.envPrinted) && obs.envContainers >= 1 && got.status === 200 && got.disposition.startsWith("attachment") && /ada,3/.test(got.body),
    "an environment: the command runs in a container of the chat's own, and a file it shares is under the answer and downloads as written",
    `printed ${obs.envPrinted.slice(0, 40)}; ${obs.envContainers} container(s); ${obs.fileLink}; ${got.status} ${got.disposition.slice(0, 40)}; row ${obs.fileRow}`,
  );
  obs.envThread = env;
  // Its egress sidecar: closed by default, and the addresses no policy can open (deny.always)
  const box = containersOf(env)[0] ?? "";
  obs.egress = sh(`docker logs sandbox-egress-${box.replace(/^sandbox-/, "")} 2>&1 | grep -oE 'default=deny|loaded [0-9]+ always-(deny|allow) rule\\(s\\)' | awk '!seen[$0]++'`).out.split("\n");
  check(obs.egress.includes("default=deny") && obs.egress.some((l) => /^loaded [1-9]\d* always-deny/.test(l)),
    "the environment's egress sidecar denies by default, and loaded the always-deny rules no policy lifts", obs.egress.join(", "));

  // 2x.4 A plugin: a marketplace in git, which an admin adds and the person then adds for themself
  rec.mark("2x.4 plugin");
  const PHRASE = `tangerine-${randomBytes(4).toString("hex")}`;
  // A one-plugin marketplace served by e2e's git server, and Ada in Admin > Plugins (market.mjs)
  const m = await market(ctx.browser, PHRASE);
  try {
    const pluginId = await m.add();
    obs.pluginSynced = appdb(`select status || ' | ' || format || ' | ' || name from plugin_sources where url = '${SOURCE}'`);
    await m.ada.goto(`${APP}/admin/plugins`, { waitUntil: "networkidle0" });
    obs.pluginListed = await m.ada.$eval('ul[aria-label="Plugins from gen9-learn-market"]', (u) => u.innerText.replace(/\s+/g, " ")).catch(() => "");
    const chosen = await m.choose(pluginId, "available");
    // The person adds it in Settings, and a new chat follows its skill
    await page.goto(`${APP}/settings`, { waitUntil: "networkidle0" });
    obs.pluginOffered = await page.$eval('ul[aria-label="Plugins"]', (u) => u.innerText.replace(/\s+/g, " ")).catch(() => "");
    await page.click('button[aria-label="Add gen9-learn-greeting"]');
    await page.waitForSelector('button[aria-label="Remove gen9-learn-greeting"]', { timeout: 15_000 });
    obs.pluginSkills = await page.$eval('ul[aria-label="Skills"]', (u) => u.innerText.replace(/\s+/g, " ")).catch(() => "");
    await newChat();
    const greeted = await send("Greet me the gen9-learn way, following your skill for it.");
    obs.pluginAnswer = await page.$$eval("ol[aria-live] > li", (lis) => lis.at(-1)?.textContent ?? "");
    obs.pluginSteps = stepsOf(greeted);
    check(
      obs.pluginSynced.startsWith("synced") && chosen === "available" && /gen9-learn-greeting · From gen9-learn-greeting/.test(obs.pluginSkills) && obs.pluginAnswer.includes(PHRASE) && /read_file \/plugins\//.test(obs.pluginSteps),
      "a plugin: Ada adds a git marketplace and makes its plugin available; the person adds it, and their chat follows its skill",
      `${obs.pluginSynced}; ${chosen}; ${obs.pluginSkills.slice(0, 70)}; answer has the phrase: ${obs.pluginAnswer.includes(PHRASE)}; ${obs.pluginSteps.slice(0, 80)}`,
    );
  } finally {
    // The source goes whatever happened, and its plugin with it, from everyone
    obs.pluginSourceLeft = await m.close().catch((e) => (obs.pluginCleanup = String(e.message)));
  }
  return obs;
}
