// Batch 2, deeper: the rest of what using Gen9 touches, as the same person, after b2.
//   search      the Search page's four modes over their chats, and what search reads (chat_search)
//   subagent    a claim handed to the fact checker, the helper the agent delegates to
//   limit       a person over their model limit: Settings' Model use, the card with its reset, Retry
//   outage      the router stopped: every attempt fails, the run waits for Retry, then goes on
//   context     a chat that outgrows a small context budget is summarized, and says so
// The outage stops gen9-models' router and the context step swaps gen9-agent's worker for one with a
// 12,000-token budget: both are always put back. What ran is read from Postgres, not from answers.
import { randomBytes } from "node:crypto";
import { APP, appdb, check, sh } from "../lib.mjs";
import { RETRY_CARD, chatHelpers, eventsOf, health, modelsdb, router, runsOf, sleep, stepsOf, until } from "../chat.mjs";

const ROUTER = "gen9-models-litellm-1";
const WORKER = "gen9-agent-worker-1";
const SMALL = "gen9-agent-worker-small-context";

export default async function deeper(ctx) {
  const { page, user } = ctx;
  const obs = {};
  const { newChat, send, button, lastAnswer } = chatHelpers(page);
  const spent = () => Number(modelsdb(`select coalesce(sum(spend), 0) from "LiteLLM_SpendLogs" where end_user = '${user.sub}'`)) || 0;
  const spentBefore = spent();

  // ---- search: what it reads, then the page's four modes over this person's chats
  const rows = () => appdb(`select count(*) || '|' || count(s.embedding) from chat_search s join users u on u.id = s.user_id where u.email = '${user.email}'`);
  obs.searchRows = await until(() => {
    const [all, embedded] = rows().split("|").map(Number);
    return all > 0 && all === embedded && rows();
  }, 120);
  obs.searchRowSample = appdb(
    `select left(s.body, 50) || '|' || s.embed_model || '|' || vector_dims(s.embedding) from chat_search s join users u on u.id = s.user_id where u.email = '${user.email}' order by s.created_at limit 1`,
  );
  obs.searchIndexes = appdb(`select string_agg(indexname || ' ' || substring(indexdef from 'USING (\\w+)'), ', ' order by indexname) from pg_indexes where tablename = 'chat_search'`);
  check(!!obs.searchRows, "each finished run is a row of chat_search, with its text and its embedding", `${obs.searchRows} (rows|embedded); ${obs.searchRowSample}; indexes: ${obs.searchIndexes}`);

  const first = ctx.threadId; // the first chat: the refresh token, then the lighthouses
  await page.goto(`${APP}/search`, { waitUntil: "networkidle0" });
  const results = () => page.$$eval('main section ul a[href^="/chat/"]', (as) => as.map((a) => ({ href: a.getAttribute("href"), text: a.textContent.trim().slice(0, 120) })));
  const statusText = () => page.$eval('main section [role="status"]', (el) => el.textContent.trim()).catch(() => "");
  const headings = () => page.$$eval("main section h2", (hs) => hs.map((h) => h.textContent.trim()));
  // By meaning, the router embeds the query first: seconds, more when the provider is slow (23 s
  // for one word)
  const shown = (q, label) =>
    page.waitForFunction((name) => [...document.querySelectorAll("main section")].some((el) => el.getAttribute("aria-label") === name), { timeout: 120_000 }, `Results for “${q}”, by ${label}`);
  let current = "All";
  const submit = async (text) => {
    if (await page.$('button[aria-label="Clear"]')) await page.click('button[aria-label="Clear"]');
    await page.type("#search-q", text);
    await page.keyboard.press("Enter");
    await shown(text, current);
  };
  const mode = async (label, q) => {
    const [option] = await page.$$(`xpath/.//fieldset//label[normalize-space()="${label}"]`);
    await option.click();
    current = label;
    await shown(q, label);
  };
  obs.searchModes = await page.$$eval("fieldset label", (ls) => ls.map((l) => l.textContent.trim()));
  // Words: an exact word of the chat (BM25)
  await submit("lighthouses");
  await mode("Words", "lighthouses");
  obs.words = { url: page.url().replace(APP, ""), status: await statusText(), first: (await results())[0] };
  check(obs.words.first?.href === `/chat/${first}`, "Words: a word the chat holds finds it", `${obs.words.status}; ${obs.words.url}`);
  // Meaning: a question in German, sharing no word with any chat (vectors; the embedding model is
  // multilingual). A single word lands nearest the shortest rows, whatever it means ("Leuchttürme"
  // ranked "Reply with one word: over" first): a whole question is what people type
  const meaningQ = "Welche Türme warnen nachts Schiffe?";
  await submit(meaningQ);
  await mode("Meaning", meaningQ);
  obs.meaning = { url: page.url().replace(APP, ""), first: (await results())[0] };
  await mode("Words", meaningQ);
  obs.meaningByWords = await statusText();
  check(
    obs.meaning.first?.href === `/chat/${first}` && /No chats match/.test(obs.meaningByWords),
    "Meaning: a question in German, sharing no word with the chat, finds it; by words, nothing",
    `${obs.meaningByWords}; ${obs.meaning.url}`,
  );
  // …and a question no chat is about finds nothing by meaning: the nearest chats are below the
  // model's floor (gen9-agent api/search.py, SIMILARITY_FLOORS)
  const aboutNothing = "Which volcanoes erupted this year?";
  await mode("Meaning", meaningQ);
  await submit(aboutNothing);
  obs.meaningNothing = await statusText();
  check(/No chats match/.test(obs.meaningNothing), "Meaning: a question no chat is about finds none", obs.meaningNothing);
  await mode("Words", aboutNothing);
  // Title: a misspelled title (trigrams): the recipe chat's
  const recipe = appdb(`select t.id from threads t join users u on u.id = t.user_id where u.email = '${user.email}' and t.title like 'I''d like a dinner recipe%'`);
  await submit("dinner recipie");
  await mode("Title", "dinner recipie");
  obs.title = { url: page.url().replace(APP, ""), status: await statusText(), first: (await results())[0] };
  check(obs.title.first?.href === `/chat/${recipe}`, "Title: a misspelled title finds its chat", `${obs.title.status}; ${obs.title.url}`);
  // All: words at once, more by meaning under them
  await mode("All", "dinner recipie");
  await submit("lighthouses");
  await page.waitForFunction(() => /More by meaning|unavailable right now/.test(document.querySelector("main section")?.textContent ?? ""), { timeout: 120_000 }).catch(() => {});
  obs.all = { url: page.url().replace(APP, ""), status: await statusText(), headings: await headings(), first: (await results())[0] };
  check(obs.all.first?.href === `/chat/${first}` && obs.all.headings.includes("More by meaning"), "All: the matches by words first, then more by meaning under them", `${obs.all.status}; ${obs.all.headings.join(" | ")}`);
  obs.searchApi = sh(`docker logs --since 5m gen9-agent-api-1 2>&1 | grep -E '"GET /v1/search' | tail -4`).out;
  // What a person searched stays out of the agent's log: the query's value is masked (main.py)
  check(/GET \/v1\/search\?q=…/.test(obs.searchApi) && !/lighthouse/i.test(obs.searchApi), "the agent's access log shows the search without what was searched", obs.searchApi.split("\n").at(-1)?.replace(/^.*"(GET [^"]*)".*$/, "$1"));

  // ---- subagent: the fact checker
  await newChat();
  const claimChat = await send("Use your fact-checker subagent to verify this claim, then reply with its verdict line only: PostgreSQL 18 was first released in September 2025.");
  obs.subagentSteps = stepsOf(claimChat);
  obs.subagentAnswer = (await lastAnswer()).slice(0, 160);
  await page.click("ol[aria-live] > li:last-child details > summary").catch(() => {});
  obs.subagentTools = await page.$$eval('ul[aria-label="Tools used"] > li', (lis) => lis.map((li) => li.textContent.trim().slice(0, 100))).catch(() => []);
  obs.subagentVersion = appdb(`select agent_version from runs where thread_id = '${claimChat}' order by created_at desc limit 1`);
  obs.subagentTrace = await until(
    () => sh(`docker exec gen9-langfuse-clickhouse-1 clickhouse-client -q "select count() from events_core where session_id = '${claimChat}' and name like '%fact-checker%'"`).out.trim() !== "0" &&
      sh(`docker exec gen9-langfuse-clickhouse-1 clickhouse-client -q "select name from events_core where session_id = '${claimChat}' and name like '%fact-checker%' limit 1"`).out.trim(),
    60,
  );
  check(/task fact-checker/.test(obs.subagentSteps) && obs.subagentTools.some((t) => t.startsWith("Asked the fact checker")), "the agent hands the claim to its fact checker, and the chat says so", `${obs.subagentSteps}; ${obs.subagentTools.find((t) => t.startsWith("Asked")) ?? obs.subagentTools.join(" | ")}`);

  // ---- limit: Settings' Model use, the router refusing, the card, Retry once lifted
  const modelUse = async () => {
    await page.goto(`${APP}/settings`, { waitUntil: "networkidle0" });
    return page.evaluate(() => {
      const label = [...document.querySelectorAll("p")].find((p) => p.textContent.trim() === "Model use");
      return label?.nextElementSibling?.textContent.trim() ?? null;
    });
  };
  obs.modelUseBefore = await modelUse();
  const tiny = `gen9-learn-tiny-${Date.now()}`;
  let limitChat;
  try {
    await router("/budget/new", { budget_id: tiny, max_budget: 0.0000001, budget_duration: "30d" });
    const set = await router("/customer/update", { user_id: user.sub, budget_id: tiny });
    if (!set.ok) await router("/customer/new", { user_id: user.sub, budget_id: tiny });
    await newChat();
    limitChat = await send("Reply with one word: over", { waitFor: RETRY_CARD, timeout: 120_000 });
    obs.limitCard = await page.$eval(RETRY_CARD, (c) => c.textContent.trim()).catch(() => null);
    obs.limitRun = runsOf(limitChat);
    obs.limitInput = appdb(`select i.kind from run_inputs i join runs r on r.id = i.run_id where r.thread_id = '${limitChat}'`);
    obs.limitRefused = modelsdb(`select count(*) from "LiteLLM_SpendLogs" where end_user = '${user.sub}' and status = 'failure' and "startTime" > now() - interval '5 minutes'`);
    obs.modelUseOver = await modelUse();
    check(
      /reached your model usage limit/.test(obs.limitCard ?? "") && /resets on/.test(obs.limitCard ?? "") && obs.limitRun === "waiting|1" && obs.limitInput === "retry",
      "over the limit, the run waits for Retry, and the card says why and when it resets",
      `${obs.limitCard?.slice(0, 200)}; run ${obs.limitRun}; Settings: ${obs.modelUseOver}`,
    );
  } finally {
    await router("/customer/delete", { user_ids: [user.sub] }).catch(() => {});
    await router("/budget/delete", { id: tiny }).catch(() => {});
  }
  if (limitChat) {
    // "load", not "networkidle0": a chat with a run going keeps its event stream open
    await page.goto(`${APP}/chat/${limitChat}`, { waitUntil: "load" });
    await page.waitForSelector(RETRY_CARD, { timeout: 15_000 });
    await (await button(RETRY_CARD, "Retry")).click();
    obs.limitAfter = await until(() => /^success\|/.test(runsOf(limitChat)) && runsOf(limitChat), 180);
    obs.limitEvents = eventsOf(limitChat);
    check(obs.limitAfter === "success|1" || /^success\|/.test(obs.limitAfter ?? ""), "the limit lifted, Retry finishes the same run", `${obs.limitAfter}; ${obs.limitEvents}`);
  }

  // ---- outage: the router stopped, every attempt fails; Retry once it's back
  let outageChat;
  try {
    sh(`docker stop ${ROUTER}`);
    await newChat();
    const startedAt = Date.now();
    outageChat = await send("Reply with exactly: OK", { waitFor: RETRY_CARD, timeout: 240_000 });
    obs.outageWaitedS = Math.round((Date.now() - startedAt) / 1000);
    obs.outageCard = await page.$eval(RETRY_CARD, (c) => c.textContent.trim()).catch(() => null);
    obs.outageRun = runsOf(outageChat);
    obs.outageComposer = await page.$eval("#composer", (t) => t.placeholder).catch(() => null);
    obs.outageLog = sh(`docker logs --since 5m ${WORKER} 2>&1 | grep -iE 'park|retry|attempt' | tail -3`).out.replace(/[0-9a-f-]{36}/g, "<id>");
    check(/didn't answer/.test(obs.outageCard ?? "") && /^waiting\|/.test(obs.outageRun), "with the router down, every attempt fails and the run waits for Retry", `${obs.outageCard?.slice(0, 160)}; run ${obs.outageRun}; after ${obs.outageWaitedS} s`);
  } finally {
    sh(`docker start ${ROUTER}`);
    await until(() => health(ROUTER) === "healthy", 120);
  }
  if (outageChat) {
    await (await button(RETRY_CARD, "Retry")).click();
    obs.outageAfter = await until(() => /^success\|/.test(runsOf(outageChat)) && runsOf(outageChat), 180);
    obs.outageEvents = eventsOf(outageChat);
    check(/^success\|/.test(obs.outageAfter ?? "") && /input\.requested,input\.provided/.test(obs.outageEvents), "the router back, Retry goes on from the checkpoint: the same run finishes", `${obs.outageAfter}; ${obs.outageEvents}`);
  }

  // ---- context: a worker with a small budget, three long messages, the summary, the note
  const code = `osprey-${randomBytes(3).toString("hex")}`;
  const filler = Array.from({ length: 150 }, (_, i) => `Line ${i}: the tide came in and went out.`).join(" ");
  try {
    sh(`docker stop ${WORKER}; docker rm -f ${SMALL}`);
    const small = sh(`docker compose -f gen9-agent/compose.yaml run -d --no-deps --name ${SMALL} -e CONTEXT_BUDGET_TOKENS=12000 worker`, { timeout: 120_000 });
    const ready = await until(() => health(SMALL) === "healthy", 120);
    check(small.code === 0 && ready, "a worker with a 12,000-token context budget runs in the worker's place", small.out.split("\n").at(-1));
    await newChat();
    let longChat;
    for (let turn = 0; turn < 3; turn++) {
      const lead = turn === 0 ? `The code word is ${code}. ` : `Note ${turn}. `;
      longChat = await send(`${lead}Here is a long note to keep: ${filler} Reply with only: noted.`);
    }
    obs.contextEvents = appdb(
      `select string_agg(e.type || ' ' || coalesce(e.data::text, ''), '; ') from run_events e join runs r on r.id = e.run_id where r.thread_id = '${longChat}' and e.type = 'context.summarized'`,
    );
    await send("What was the code word I gave you at the start? Reply with it only.");
    obs.contextRemembered = (await lastAnswer()).includes(code);
    await page.reload({ waitUntil: "networkidle0" });
    obs.contextNotes = await page.$$eval('[role="note"]', (ns) => ns.map((n) => n.textContent.trim()).filter((t) => /summarized/.test(t)));
    obs.contextMessages = await page.$$eval("ol[aria-live] > li", (lis) => lis.length);
    obs.contextCheckpoints = appdb(`select count(*) from langgraph.checkpoints where thread_id = '${longChat}'`);
    check(
      !!obs.contextEvents && obs.contextRemembered && obs.contextNotes.length >= 1 && obs.contextMessages === 8,
      "outgrowing the budget, a run is summarized and says so; the code word from before is still known, and the whole chat still shows",
      `${obs.contextEvents?.slice(0, 160)}; notes: ${obs.contextNotes.join(" | ")}; ${obs.contextMessages} messages`,
    );
  } finally {
    sh(`docker rm -f ${SMALL}; docker start ${WORKER}`);
    check(await until(() => health(WORKER) === "healthy", 120), "the worker is back, as it was");
  }

  // What this batch cost in model calls (the router's log writes in batches: give it a moment)
  await sleep(15_000);
  obs.spentUsd = Math.round((spent() - spentBefore) * 10000) / 10000;
  console.log(`      (model spend of this batch: $${obs.spentUsd})`);
  return obs;
}
