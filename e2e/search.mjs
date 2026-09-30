// Search over past chats, through gen9-agent's API and `gen9 search`:
//   1. a throwaway user (Admin API) signs in on the terminal and asks three questions, each with
//      an exact reply, and a second one in the first chat;
//      each is indexed (text, then its embedding) within a minute and a half
//   2. each kind of search finds the right chat: an exact term (BM25), a paraphrase and a
//      question sharing no word with the chat (vector), a misspelled title (trigram), words and
//      meaning together (hybrid); a chat with two matching turns is one hit. The queries can use the
//      BM25 and HNSW indexes (EXPLAIN, with sorting off so a small table can't hide them)
//   3. `gen9 search` prints the chat and how to continue it; the web app's Search screen finds the
//      same chats in Chrome, by its on-screen words (docs/design/screens/search.md): "All" shows
//      the matches by words at once and more by meaning under them, nothing moving
//   4. the seeded user never sees these chats, and this user never sees theirs
//   5. with SEARCH_RERANK on in gen9-agent, results come back reranked (skipped when it's off)
//   6. a deleted chat leaves every mode at once; deleting the account removes the rest
// The user is deleted at the end, whatever happens. It reads the bootstrap admin and the seeded
// user from gen9-keycloak/.env. It costs four short fixed replies and a few embeddings.
import { spawn, spawnSync } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { launch } from "./browser.mjs";
import { ROOT, signInTerminal } from "./signin.mjs";

const env = Object.fromEntries(
  readFileSync(`${ROOT}gen9-keycloak/.env`, "utf8")
    .split("\n")
    .filter((line) => /^[A-Z0-9_]+=/.test(line))
    .map((line) => [line.slice(0, line.indexOf("=")), line.slice(line.indexOf("=") + 1)]),
);
const API = process.env.GEN9_API ?? "http://localhost:17000";
const APP = process.env.APP_URL ?? "http://localhost:14000";
const KEYCLOAK = process.env.KEYCLOAK_URL ?? "http://localhost:15000";
const EMAIL = `search-${Date.now()}@gen9.test`;
const PASSWORD = `e2e-${randomBytes(12).toString("hex")}`;
const MODES = ["hybrid", "keyword", "semantic", "fuzzy"];
// Each asks for an exact reply, so every word a chat holds is known: "sharing no word" below can be
// true by construction, whatever the model would have said
const QUESTIONS = {
  postgres: "How do I tune Postgres autovacuum for a table with heavy updates? Reply with exactly: Lower the scale factor and raise the cost limit.",
  sourdough: "My sourdough starter smells like acetone, what should I do? Reply with exactly: Feed it more often and keep it cooler.",
  temporal: "How do Temporal activities retry after a failure? Reply with exactly: By their retry policy, with backoff.",
};
// The first chat's second turn: its words match "autovacuum" too
const FOLLOW_UP = "And for a small, mostly read table? Reply with exactly: Autovacuum defaults are fine there.";

let failures = 0;
function check(ok, what, detail = "") {
  console.log(`${ok ? "ok  " : "FAIL"}  ${what}${detail ? `: ${detail}` : ""}`);
  if (!ok) failures++;
  return ok;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Keycloak Admin API as the bootstrap admin: the throwaway user
// admin-cli's tokens live 60 s and a run takes minutes: a new one when it's about to expire, or the
// cleanup at the end fails silently and leaves the throwaway user behind
async function admin(path, init = {}) {
  if (!admin.token || Date.now() > admin.expires) {
    const body = await fetch(`${KEYCLOAK}/realms/master/protocol/openid-connect/token`, {
      method: "POST",
      body: new URLSearchParams({
        grant_type: "password",
        client_id: "admin-cli",
        username: env.KC_BOOTSTRAP_ADMIN_USERNAME,
        password: env.KC_BOOTSTRAP_ADMIN_PASSWORD,
      }),
    }).then((r) => r.json());
    admin.token = body.access_token;
    admin.expires = Date.now() + ((body.expires_in ?? 60) - 10) * 1000;
  }
  const response = await fetch(`${KEYCLOAK}/admin/realms/gen9${path}`, {
    ...init,
    headers: { Authorization: `Bearer ${admin.token}`, "Content-Type": "application/json" },
  });
  if (!response.ok && response.status !== 404) throw new Error(`Keycloak ${init.method ?? "GET"} ${path}: ${response.status}`);
  return response.status === 201 || response.status === 204 || response.status === 404 ? null : response.json();
}

// gen9-postgres, read as the superuser inside its container (never prints a secret)
const psql = (query) =>
  spawnSync("docker", ["exec", "gen9-postgres-postgres-1", "psql", "-U", "postgres", "-d", "gen9_agent", "-tAc", query], {
    encoding: "utf8",
  }).stdout.trim();

// `gen9 …` as a user (their GEN9_CONFIG_DIR); resolves to its output
const gen9 = (configDir, ...args) =>
  new Promise((resolve) => {
    const child = spawn("uv", ["run", "-q", "gen9", ...args], {
      cwd: `${ROOT}gen9-cli`,
      env: { ...process.env, GEN9_CONFIG_DIR: configDir },
    });
    let out = "";
    child.stdout.on("data", (d) => (out += d));
    child.stderr.on("data", (d) => (out += d));
    child.on("exit", (code) => resolve({ code, out }));
  });

// A fresh access token: `gen9 whoami` refreshes it when it expired
async function token(configDir) {
  await gen9(configDir, "whoami");
  return JSON.parse(readFileSync(join(configDir, "credentials.json"), "utf8")).access_token;
}

async function search(bearer, q, mode) {
  const url = `${API}/v1/search?${new URLSearchParams({ q, mode, limit: "10" })}`;
  const response = await fetch(url, { headers: { Authorization: `Bearer ${bearer}` } });
  return response.ok ? response.json() : { status: response.status, body: await response.text() };
}

const userDir = mkdtempSync(join(tmpdir(), "gen9-search-user-"));
const seededDir = mkdtempSync(join(tmpdir(), "gen9-search-seeded-"));
let userId;
try {
  await admin("/users", {
    method: "POST",
    body: JSON.stringify({
      username: EMAIL,
      email: EMAIL,
      firstName: "Search",
      lastName: "Check",
      enabled: true,
      emailVerified: true,
      credentials: [{ type: "password", value: PASSWORD, temporary: false }],
    }),
  });
  userId = (await admin(`/users?email=${encodeURIComponent(EMAIL)}&exact=true`))[0].id;
  const signedIn = await signInTerminal({ email: EMAIL, password: PASSWORD, configDir: userDir });
  check(Boolean(signedIn), "a throwaway user signs in on the terminal (device flow)", signedIn ?? "");

  // 1. Three chats, each indexed
  const threads = {};
  for (const [name, question] of Object.entries(QUESTIONS)) {
    const { out } = await gen9(userDir, "ask", question);
    threads[name] = out.match(/--thread ([0-9a-f-]{36})/)?.[1];
  }
  check(Object.values(threads).every(Boolean), "three chats answered", Object.keys(threads).join(", "));
  // A second turn that matches the same words: the chat must still be one hit (step 2)
  await gen9(userDir, "ask", "--thread", threads.postgres, FOLLOW_UP);
  const uid = psql(`select id from users where email = '${EMAIL}'`);
  let embedded = 0;
  for (let i = 0; i < 45 && embedded < 4; i++) {
    embedded = Number(psql(`select count(*) from chat_search where user_id = '${uid}' and embedding is not null`));
    if (embedded < 4) await sleep(2000);
  }
  check(embedded === 4, "each turn is indexed with its embedding (index_run after each run)", `${embedded} of 4`);

  // 2. Every kind of search
  let bearer = await token(userDir);
  const first = (hits) => (Array.isArray(hits) ? hits[0]?.thread_id : undefined);
  const keyword = await search(bearer, "autovacuum", "keyword");
  check(first(keyword) === threads.postgres, "keyword: an exact term finds its chat (BM25)", `${keyword.length} hit(s)`);
  const semantic = await search(bearer, "my bread culture smells like nail polish", "semantic");
  check(first(semantic) === threads.sourdough, "semantic: a paraphrase finds its chat first (vector)", semantic[0]?.score?.toFixed(2));
  // No word in common, proven by keyword search not finding it: meaning alone finds it
  const noWords = "fermented dough culture giving off a solvent odor";
  const byWords = await search(bearer, noWords, "keyword");
  const byMeaning = await search(bearer, noWords, "semantic");
  check(
    Array.isArray(byWords) && !byWords.some((h) => h.thread_id === threads.sourdough) && first(byMeaning) === threads.sourdough,
    "semantic: a question sharing no word with a chat finds it by meaning (keyword doesn't)",
    `keyword ${byWords.length} hit(s), semantic first ${byMeaning[0]?.score?.toFixed(2)}`,
  );
  const fuzzy = await search(bearer, "sourdugh startr", "fuzzy");
  check(first(fuzzy) === threads.sourdough, "fuzzy: a misspelled title finds its chat (trigram)", fuzzy[0]?.score?.toFixed(2));
  const hybrid = await search(bearer, "autovacuum settings for a busy table", "hybrid");
  check(first(hybrid) === threads.postgres, "hybrid: the chat matching words and meaning comes first (RRF)", hybrid.map?.((h) => h.ranked_by)[0]);
  // Search finds chats: runs of one chat are collapsed to its best (found by hand: manual-e2e.md, P2-I5)
  const repeated = [];
  for (const mode of ["keyword", "semantic", "hybrid"]) {
    const ids = [await search(bearer, "autovacuum", mode)].flat().map((h) => h?.thread_id);
    if (ids.filter((id) => id === threads.postgres).length !== 1 || new Set(ids).size !== ids.length) repeated.push(`${mode}: ${ids.join(" ")}`);
  }
  check(repeated.length === 0, "a chat with two matching turns is one hit, in each mode", repeated.join("; "));

  const bm25 = psql(
    `begin; set local enable_sort = off; explain select run_id from chat_search s where s.user_id = '${uid}'` +
      ` and (s.body <@> to_bm25query('autovacuum', 'chat_search_bm25')) < 0` +
      ` order by s.body <@> to_bm25query('autovacuum', 'chat_search_bm25') limit 5; rollback`,
  );
  check(/chat_search_bm25/.test(bm25), "keyword's query can use the BM25 index (pg_textsearch)", bm25.match(/Index Scan using \S+/)?.[0]);
  const [model, dims] = psql(`select embed_model || '|' || vector_dims(embedding) from chat_search where user_id = '${uid}' limit 1`).split("|");

  // Re-embedding's indexes: the worker's role owns no table, so it builds and drops them through
  // the owner's functions (migration c3e7a1f9d2b8). Drop this model's index and plant a stale one
  // for a model no row uses, as the superuser; the reindex Schedule, triggered now, must rebuild
  // the first and drop the second (it runs by itself too, so a stopped check heals on its own)
  const indexOf = (m) => `chat_search_hnsw_${createHash("sha256").update(m).digest("hex").slice(0, 12)}`;
  const stale = indexOf("e2e/no-such-model");
  psql(`create index if not exists ${stale} on chat_search using hnsw ((embedding::vector(3)) vector_cosine_ops) where embed_model = 'e2e/no-such-model'`);
  psql(`drop index if exists ${indexOf(model)}`);
  const otherModels = psql(`select count(*) from chat_search where embedding is not null and embed_model is distinct from '${model}'`);
  try {
    const trigger = spawnSync("docker", ["compose", "-f", `${ROOT}gen9-temporal/compose.yaml`, "run", "--rm", "cli", "temporal", "schedule", "trigger", "--schedule-id", "reindex-search"], { encoding: "utf8" });
    const indexes = () => psql(`select string_agg(c.relname || ':' || i.indisvalid, ' ' order by c.relname) from pg_index i join pg_class c on c.oid = i.indexrelid where i.indrelid = 'chat_search'::regclass and c.relname like 'chat_search_hnsw_%'`);
    let now = indexes();
    const done = () => now.includes(`${indexOf(model)}:true`) && (otherModels !== "0" || !now.includes(stale));
    for (let i = 0; i < 120 && !done(); i++) {
      await sleep(1000);
      now = indexes();
    }
    check(
      trigger.status === 0 && done(),
      `the reindex, as the worker's role, rebuilt this model's index${otherModels === "0" ? " and dropped the stale one" : " (other models' rows remain, so indexes are kept)"}`,
      now.replaceAll(indexOf(model), "<this model>").replaceAll(stale, "<stale>") || "no index",
    );
  } finally {
    psql(`drop index if exists ${stale}`);
  }
  // Asked once the reindex has built the index: a model's index comes with the reindex Schedule's
  // first firing, so an install younger than its interval (15 minutes) has none yet, and until
  // then its few rows are searched exactly
  const hnsw = psql(
    `begin; set local enable_sort = off; explain select run_id from chat_search s where s.user_id = '${uid}'` +
      ` and s.embed_model = '${model}' order by s.embedding::vector(${dims}) <=>` +
      ` (select embedding::vector(${dims}) from chat_search where user_id = '${uid}' limit 1) limit 5; rollback`,
  );
  check(/chat_search_hnsw_/.test(hnsw), "semantic's query can use its model's HNSW index", hnsw.match(/Index Scan using chat_search_hnsw_\S+/)?.[0] ?? hnsw.match(/Index Scan using \S+/)?.[0]);

  // 3. The terminal
  const cli = await gen9(userDir, "search", "autovacuum", "--mode", "keyword");
  check(cli.code === 0 && cli.out.includes(`gen9 ask --thread ${threads.postgres}`), "`gen9 search` prints the chat and how to continue it");

  // The Search screen, in Chrome
  // Desktop: the sidebar is visible (on phones it's in a sheet)
  const browser = await launch({ headless: !process.env.HEADED, defaultViewport: { width: 1280, height: 900 } });
  try {
    const page = await browser.newPage();
    await page.goto(`${APP}/auth/login`, { waitUntil: "networkidle0" });
    await page.type("#username", EMAIL);
    await page.type("#password", PASSWORD);
    await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }), page.click("#kc-login")]);
    const [link] = await page.$$('xpath/.//nav//a[normalize-space()="Search"]');
    await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }), link.click()]);
    const focused = await page.evaluate(() => document.activeElement?.id);
    check(page.url() === `${APP}/search` && focused === "search-q", "the sidebar's Search opens the screen, focus in the field", `${page.url()}, focus ${focused}`);
    const results = async () =>
      page.$$eval('main section ul a[href^="/chat/"]', (as) => as.map((a) => ({ href: a.getAttribute("href"), text: a.textContent })));
    const statusText = () => page.$eval('main section [role="status"]', (el) => el.textContent.trim());
    // The results for this query and mode are on the page: each outcome is a section named for
    // both (a client-side navigation can keep the old results on screen meanwhile)
    const shown = (q, label) =>
      page.waitForFunction((name) => [...document.querySelectorAll("main section")].some((el) => el.getAttribute("aria-label") === name), { timeout: 30_000 }, `Results for “${q}”, by ${label}`);
    let query = "";
    let current = "All";
    const submit = async (text, label = current) => {
      query = text;
      // Clear what's there with the field's own button (it puts focus back in the field)
      if (await page.$('button[aria-label="Clear"]')) await page.click('button[aria-label="Clear"]');
      await page.type("#search-q", text);
      await page.keyboard.press("Enter");
      await shown(text, label);
    };
    await submit("autovacuum");
    // All: the matches by words at once, then more by meaning under them (P3-D9)
    const wordsAt = Date.now();
    const looking = Boolean(await page.$('main section [aria-busy="true"]'));
    let found = await results();
    check(
      found[0]?.href === `/chat/${threads.postgres}` && /autovacuum/i.test(found[0]?.text) && /^\d+ chats? match(es)? its words$/.test(await statusText()),
      "All: Enter shows the chat matching its words, with its snippet and the count",
      `${await statusText()}; ${page.url().replace(APP, "")}`,
    );
    await page.waitForFunction(() => /More by meaning|unavailable right now/.test(document.querySelector("main section")?.textContent ?? ""), { timeout: 30_000 });
    const later = await results();
    check(
      later[0]?.href === found[0]?.href && (await page.$$eval("main section h2", (hs) => hs.map((h) => h.textContent))).includes("More by meaning"),
      "All: more by meaning comes under them, the matches by words staying where they were",
      `${looking ? "said it was looking by meaning; " : ""}${Date.now() - wordsAt} ms after the words`,
    );
    const mode = async (label) => {
      const [option] = await page.$$(`xpath/.//fieldset//label[normalize-space()="${label}"]`);
      await option.click();
      current = label;
      await shown(query, label);
    };
    await submit("sourdugh startr");
    await mode("Title");
    found = await results();
    check(found[0]?.href === `/chat/${threads.sourdough}` && page.url().includes("mode=fuzzy"), "Title: a misspelled title finds its chat; the mode is in the URL");
    await submit("fermented dough culture giving off a solvent odor");
    await mode("All");
    await page.waitForFunction(() => /More by meaning|unavailable right now/.test(document.querySelector("main section")?.textContent ?? ""), { timeout: 30_000 });
    found = await results();
    check(
      /^No chats match its words$/.test(await statusText()) && found[0]?.href === `/chat/${threads.sourdough}`,
      "All: a question sharing no word with the chat finds it by meaning",
      await statusText(),
    );
    await mode("Meaning");
    found = await results();
    check(found[0]?.href === `/chat/${threads.sourdough}`, "Meaning: a question sharing no word with the chat finds it");
    await mode("Words");
    check((await results()).length === 0 && /No chats match/.test(await statusText()), "Words: the same question finds nothing, and says so", await statusText());
    await page.goBack();
    await shown(query, "Meaning");
    check(page.url().includes("mode=semantic") && (await results())[0]?.href === `/chat/${threads.sourdough}`, "Back returns to the previous results");
    const [first] = await page.$$('main section ul a[href^="/chat/"]');
    await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }), first.click()]);
    check(page.url() === `${APP}/chat/${threads.sourdough}`, "a result opens its chat");
  } catch (e) {
    if (process.env.DEBUG_DIR) {
      const [page] = (await browser.pages()).slice(-1);
      await page.screenshot({ path: `${process.env.DEBUG_DIR}/search-failure.png` });
      console.log("debug:", page.url(), await page.evaluate(() => [document.querySelector("main section")?.getAttribute("aria-label"), document.querySelector("#search-q")?.value]));
    }
    throw e;
  } finally {
    await browser.close();
  }

  // 4. Only one's own chats
  const seeded = await signInTerminal({ email: env.GEN9_SEED_USER_EMAIL, password: env.GEN9_SEED_USER_PASSWORD, configDir: seededDir });
  check(Boolean(seeded), "the seeded user signs in on the terminal", seeded ?? "");
  const seededBearer = await token(seededDir);
  const ours = new Set(Object.values(threads));
  let leaks = 0;
  let foreign = 0;
  for (const mode of MODES) {
    for (const q of ["sourdough starter", "autovacuum", "Temporal activities retry"]) {
      const theirs = await search(seededBearer, q, mode);
      leaks += (Array.isArray(theirs) ? theirs : []).filter((h) => ours.has(h.thread_id)).length;
      const mine = await search(bearer, q, mode);
      foreign += (Array.isArray(mine) ? mine : []).filter((h) => !ours.has(h.thread_id)).length;
    }
  }
  check(leaks === 0, "the seeded user never sees this user's chats (12 searches)", `${leaks} leaked`);
  check(foreign === 0, "this user sees only their own chats (12 searches)", `${foreign} foreign`);

  // 5. Rerank, when gen9-agent has it on
  const rerankOn = spawnSync("docker", ["exec", "gen9-agent-api-1", "printenv", "SEARCH_RERANK"], { encoding: "utf8" }).stdout.trim() === "true";
  if (rerankOn) {
    const reranked = await search(bearer, "autovacuum settings for a busy table", "hybrid");
    check(reranked.every?.((h) => h.ranked_by === "rerank"), "with SEARCH_RERANK on, results come back reranked");
  } else {
    console.log("skip  rerank: SEARCH_RERANK is off in gen9-agent (gen9-models' `local` profile serves one)");
  }

  // 6. Deletion
  bearer = await token(userDir);
  const deleted = await fetch(`${API}/v1/threads/${threads.sourdough}`, { method: "DELETE", headers: { Authorization: `Bearer ${bearer}` } });
  const gone = [];
  for (const mode of MODES) {
    const hits = await search(bearer, mode === "fuzzy" ? "sourdugh startr" : "sourdough starter acetone", mode);
    gone.push(Array.isArray(hits) && !hits.some((h) => h.thread_id === threads.sourdough));
  }
  check([202, 204].includes(deleted.status) && gone.every(Boolean), "a deleted chat leaves every mode at once", `DELETE ${deleted.status}`);
  let rows = "1";
  for (let i = 0; i < 30 && rows !== "0"; i++) {
    rows = psql(`select count(*) from chat_search where thread_id = '${threads.sourdough}'`);
    if (rows !== "0") await sleep(1000);
  }
  check(rows === "0", "its search rows go with it", `${rows} left`);
  const account = await fetch(`${API}/v1/me`, { method: "DELETE", headers: { Authorization: `Bearer ${bearer}` } });
  rows = "1";
  for (let i = 0; i < 60 && rows !== "0"; i++) {
    rows = psql(`select count(*) from chat_search where user_id = '${uid}'`);
    if (rows !== "0") await sleep(1000);
  }
  check([202, 204].includes(account.status) && rows === "0", "deleting the account removes the rest of its search rows", `DELETE /v1/me ${account.status}, ${rows} left`);
} catch (e) {
  check(false, "the search check ran to the end", e.message);
} finally {
  if (userId) await admin(`/users/${userId}`, { method: "DELETE" }).catch(() => {});
  await gen9(seededDir, "logout").catch(() => {});
  rmSync(userDir, { recursive: true, force: true });
  rmSync(seededDir, { recursive: true, force: true });
}

console.log(failures ? `\n${failures} check(s) failed` : "\nall checks passed");
process.exit(failures ? 1 : 0);
