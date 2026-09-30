// Batch 2, using it: ask a question (streamed), reload the chat, see it in every store, and watch a
// silent token refresh after the access token expires (set QUICK=1 to skip the ~5-minute wait).
import { randomBytes } from "node:crypto";
import { APP, appdb, ch, check, kcdb, sh, valkey } from "../lib.mjs";

const sessionOf = (sub) => valkey(`SMEMBERS gen9:session-by-sub:${sub}`).split("\n").filter(Boolean)[0];
const peek = (email) => {
  const out = sh(`gen9-learn/tools/peek-session.sh ${email}`).out;
  const json = out.slice(out.indexOf("{"));
  try {
    return JSON.parse(json);
  } catch {
    return null;
  }
};

export default async function using(ctx) {
  const { page, rec, user } = ctx;
  const obs = {};
  const question = "In one short sentence: what is a refresh token?";

  rec.mark("2.1 ask");
  await page.goto(`${APP}/chat`, { waitUntil: "networkidle0" });
  await page.type("#composer", question);
  await page.click('button[aria-label="Send"]');
  await page.waitForFunction(
    () => !document.querySelector('button[aria-label="Stop"]') && document.querySelectorAll("ol[aria-live] > li").length >= 2 && !document.querySelector("ol[aria-live] .animate-pulse"),
    { timeout: 180_000, polling: 500 },
  );
  await page.waitForNetworkIdle({ idleTime: 500 });
  obs.answer = await page.$eval("ol[aria-live] > li:last-child", (li) => li.textContent.trim().replace(/^Gen9 said: /, "").slice(0, 160));
  obs.threadUrl = page.url().replace(/[0-9a-f-]{36}$/, "<thread id>");
  const threadId = page.url().split("/").pop();
  ctx.threadId = threadId;
  obs.askHops = rec.hops("2.1 ask", ["Fetch", "XHR", "Document", "EventSource"]).map(({ id, ...hop }) => ({ ...hop, url: hop.url.replace(threadId, "<thread id>") }));
  const run = rec.log.find((item) => item.step === "2.1 ask" && item.url.endsWith("/runs"));
  const stream = run ? await rec.body(run.id).catch((e) => `(${e.message})`) : "";
  obs.streamEvents = [...stream.matchAll(/^event: (\S+)/gm)].map((m) => m[1]).reduce((acc, name) => {
    acc[name] = (acc[name] ?? 0) + 1;
    return acc;
  }, {});
  obs.streamSample = stream.split("\n\n").slice(0, 3).join("\n\n").slice(0, 400);
  check(
    !!obs.streamEvents["message.delta"] && obs.streamEvents["run.completed"] === 1,
    "the answer streamed as the run's server-sent events",
    JSON.stringify(obs.streamEvents),
  );
  obs.agentLogAsk = sh(`docker logs --since 3m gen9-agent-api-1 2>&1 | grep -E '"(GET|POST) /v1/threads' | tail -4`).out.replaceAll(threadId, "<thread id>");
  obs.workerLogAsk = sh(`docker logs --since 3m gen9-agent-worker-1 2>&1 | grep -E 'attempt' | tail -1`).out.replace(/run \S+/, "run <run id>").replaceAll(threadId, "<thread id>");
  obs.run = appdb(`select status || '|' || attempts from runs where thread_id = '${threadId}'`);
  obs.runEvents = appdb(
    `select string_agg(type || ' ' || n, ', ' order by first) from (select type, count(*) n, min(seq) first from run_events e join runs r on r.id = e.run_id where r.thread_id = '${threadId}' group by type) t`,
  );
  check(obs.run === "success|1" && /attempt 1/.test(obs.workerLogAsk), "a worker ran it as a run, once, and it succeeded", `${obs.run}; events: ${obs.runEvents}`);
  // The same run as Temporal sees it: one workflow per run, found by the user's sub
  // After the answer the workflow still makes the chat searchable (index_run): wait for its end
  for (let i = 0; i < 20; i++) {
    obs.temporalRuns = sh(
      `docker compose -f gen9-temporal/compose.yaml run --rm cli temporal workflow list --query "Gen9User='${user.sub}'"`,
    ).out.replace(/run-[0-9a-f-]+/g, "run-<run id>").replace(/\d+ (seconds?|minutes?) ago/g, "<when>");
    if (/Completed\s+run-<run id>/.test(obs.temporalRuns)) break;
    await new Promise((r) => setTimeout(r, 3000));
  }
  check(
    /Completed\s+run-<run id>\s+RunWorkflow/.test(obs.temporalRuns),
    "Temporal ran it as the workflow run-<run id>, found by the user's sub (Gen9User)",
    obs.temporalRuns.replaceAll("\n", " | "),
  );
  // The same question as gen9-models saw it (page step 2.1): answered by alias, used under the sub
  // The router writes its spend logs in batches: poll for a minute
  for (let i = 0; i < 30; i++) {
    obs.routerRows = sh(
      `docker exec gen9-models-postgres-1 psql -U litellm -d litellm -tAc "select model_group || '|' || model from \\"LiteLLM_SpendLogs\\" where end_user = '${user.sub}' and status = 'success' order by \\"startTime\\""`,
    ).out.split("\n").filter(Boolean);
    if (obs.routerRows.some((row) => row.startsWith("chat|"))) break;
    await new Promise((r) => setTimeout(r, 2000));
  }
  // The answer by `chat`; making the chat searchable by meaning adds `embed` (search indexing)
  check(
    obs.routerRows.some((row) => row.startsWith("chat|")) && obs.routerRows.every((row) => /^(chat|embed)\|/.test(row)),
    "gen9-models answered it by alias, and recorded its use under the user's sub",
    obs.routerRows.join(", "),
  );


  rec.mark("2.2 reload");
  await page.reload({ waitUntil: "networkidle0" });
  obs.reloadHops = rec.hops("2.2 reload", ["Document", "Fetch"]).map(({ id, ...hop }) => ({ ...hop, url: hop.url.replace(threadId, "<thread id>") }));
  const saved = await page.$$eval("ol[aria-live] > li", (items) => items.length);
  check(saved >= 2, "on reload the chat is rendered on the server from the agent's memory", `${saved} messages`);

  obs.thread = appdb(`select title, created_at < updated_at from threads where id = '${threadId}'`);
  obs.checkpoints = appdb(
    `select (select count(*) from langgraph.checkpoints where thread_id = '${threadId}') || ' checkpoints, ' || (select count(*) from langgraph.checkpoint_writes where thread_id = '${threadId}') || ' writes, ' || (select count(*) from langgraph.checkpoint_blobs where thread_id = '${threadId}') || ' blobs'`,
  );
  check(/^[1-9]/.test(obs.checkpoints), "LangGraph saved the conversation in Postgres (schema langgraph)", obs.checkpoints);
  obs.agentLog = sh(`docker logs --since 5m gen9-agent-api-1 2>&1 | grep -E '"(GET|POST) /v1/threads' | tail -4`).out.replaceAll(threadId, "<thread id>");

  // 2.2b Reload in the middle of an answer: the page follows the same run again
  rec.mark("2.2b reload mid-answer");
  const long = "Write about 150 words on lighthouses. Plain paragraphs.";
  await page.type("#composer", long);
  await page.click('button[aria-label="Send"]');
  await page.waitForFunction(
    () => (document.querySelector("ol[aria-live] > li:last-child")?.textContent ?? "").length > 40,
    { timeout: 120_000, polling: 250 },
  );
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForSelector("ol[aria-live] > li");
  const answeringAfterReload = !!(await page.$('button[aria-label="Stop"]'));
  await page.waitForFunction(() => !document.querySelector('button[aria-label="Stop"]'), { timeout: 180_000, polling: 500 });
  await page.waitForNetworkIdle({ idleTime: 500 });
  obs.midAnswerHops = rec
    .hops("2.2b reload mid-answer", ["Document", "Fetch"])
    .filter((h) => /\/runs|\/chat\//.test(h.url) && !h.url.includes("_rsc"))
    .map(({ id, ...hop }) => ({ ...hop, url: hop.url.replace(threadId, "<thread id>").replace(/runs\/[0-9a-f-]{36}/, "runs/<run id>") }));
  const follow = obs.midAnswerHops.find((h) => h.url.includes("/stream"));
  const questions = (await page.$$eval("ol[aria-live] > li", (lis) => lis.map((li) => li.textContent.trim().replace(/^You said: /, "")))).filter((q) => q === long).length;
  obs.midAnswerRuns = appdb(`select string_agg(status, ',' order by created_at) from runs where thread_id = '${threadId}'`);
  check(
    answeringAfterReload && !!follow && questions === 1 && obs.midAnswerRuns === "success,success",
    "reload mid-answer: the page follows the same run again (…/stream?after=0) and it completes",
    `${follow?.method} ${follow?.url} ${follow?.status}; question shown ${questions}×; runs: ${obs.midAnswerRuns}`,
  );

  // Langfuse ingests asynchronously: poll ClickHouse for the run's observations
  let traced = "";
  for (let i = 0; i < 45 && !/GENERATION/.test(traced); i++) {
    traced = ch(`select type, count() from events_core where session_id = '${threadId}' group by type order by type format TSV`);
    if (!/GENERATION/.test(traced)) await new Promise((resolve) => setTimeout(resolve, 2000));
  }
  obs.traceRows = traced;
  obs.traceModel = ch(`select provided_model_name, usage_details from events_core where session_id = '${threadId}' and type = 'GENERATION' limit 1 format TSV`);
  obs.traceUser = ch(`select count() from events_core where session_id = '${threadId}' and user_id = '${user.sub}' format TSV`);
  check(!!traced && obs.traceUser !== "0", "Langfuse has the run, under the user's sub and the chat's id as session", traced.replaceAll("\n", ", "));
  check(!!obs.traceModel && !obs.traceModel.startsWith("chat\t"), "Langfuse names the model that answered, not the router's alias", obs.traceModel.split("\t")[0]);


  // 2.3b Ask before acting: a memory write waits for Allow
  const APPROVAL = 'section[aria-label="Gen9 needs your approval"]';
  const QUESTION = 'form[aria-label="Gen9 needs your answer"]';
  const lastRun = (thread) => appdb(`select id || '|' || status from runs where thread_id = '${thread}' order by created_at desc limit 1`).split("|");
  const ended = async (thread) => {
    for (let i = 0; i < 180; i++) {
      const [, status] = lastRun(thread);
      if (["success", "error", "cancelled", "expired"].includes(status)) return status;
      await new Promise((r) => setTimeout(r, 1000));
    }
    return "timeout";
  };
  const newChat = async () => page.goto(`${APP}/chat`, { waitUntil: "networkidle0" });
  // A card's button, clicked as a person would once it's ready: enabled, and still there after
  // the chat's stream settles; until the card goes (a click on a card being re-rendered is lost)
  const press = async (card, text) => {
    for (let attempt = 0; attempt < 5; attempt++) {
      await page.waitForNetworkIdle({ idleTime: 500 }).catch(() => {});
      const button = await page.waitForFunction(
        (card, text) => [...document.querySelectorAll(`${card} button`)].find((b) => b.textContent.trim() === text && !b.disabled),
        { timeout: 30_000 },
        card,
        text,
      );
      await button.asElement()?.click().catch(() => {});
      const gone = await page.waitForFunction((card) => !document.querySelector(card), { timeout: 15_000 }, card).then(() => true, () => false);
      if (gone) return;
    }
    throw new Error(`the ${text} button of ${card} didn't take`);
  };
  const colour = `teal-${randomBytes(2).toString("hex")}`;
  obs.colour = "teal-<tag>";
  rec.mark("2.3b ask before acting");
  await newChat();
  await page.click('button[aria-label^="Permission mode"]');
  await page.waitForSelector('[role="menuitemradio"]');
  for (const item of await page.$$('[role="menuitemradio"]')) {
    if ((await item.evaluate((e) => e.textContent)).includes("Ask before acting")) await item.click();
  }
  await page.waitForFunction(() => document.querySelector('button[aria-label^="Permission mode"]')?.getAttribute("aria-label").endsWith("Ask before acting"));
  await page.type("#composer", `Remember that my favourite colour is ${colour}. Reply with exactly: Noted.`);
  await page.keyboard.press("Enter");
  await page.waitForSelector(APPROVAL, { timeout: 180_000 });
  const askThread = page.url().split("/").pop();
  const [askRun, waiting] = lastRun(askThread);
  obs.approvalCard = (await page.$eval(APPROVAL, (c) => c.innerText.replace(/\s+/g, " "))).replace(colour, "teal-<tag>").slice(0, 200);
  obs.approvalPlaceholder = await page.$eval("#composer", (t) => t.placeholder);
  obs.approvalWaiting = waiting;
  obs.approvalInput = appdb(`select kind from run_inputs where run_id = '${askRun}'`);
  obs.approvalTemporal = sh(`docker compose -f gen9-temporal/compose.yaml run --rm cli temporal workflow list --query "WorkflowId='run-${askRun}'"`).out.replace(/run-[0-9a-f-]+/g, "run-<run id>").replace(/\d+ (seconds?|minutes?) ago/g, "<when>");
  check(
    obs.approvalCard.includes("Gen9 wants to update your memory") && waiting === "waiting" && obs.approvalInput === "approval" && /Running\s+run-<run id>/.test(obs.approvalTemporal),
    "in Ask before acting, the memory write waits: the run is waiting, an approval is recorded, its workflow runs on",
    `${waiting}; ${obs.approvalInput}; ${obs.approvalTemporal.replaceAll("\n", " | ")}`,
  );
  await press(APPROVAL, "Allow");
  obs.approvalEnded = await ended(askThread);
  obs.approvalEvents = appdb(`select string_agg(type, ', ' order by seq) from run_events where run_id = '${askRun}' and type in ('input.requested', 'input.provided', 'run.completed')`);
  obs.memoryHasIt = appdb(`select count(*) from langgraph.store where prefix = 'memories.${user.sub}' and value::text like '%${colour}%'`);
  check(
    obs.approvalEnded === "success" && obs.approvalEvents === "input.requested, input.provided, run.completed" && obs.memoryHasIt === "1",
    "Allow: the run goes on, and the colour is in their memory",
    `${obs.approvalEnded}; ${obs.approvalEvents}; memory ${obs.memoryHasIt}`,
  );

  // 2.3c A question mid-task, answered
  rec.mark("2.3c a question");
  await newChat();
  await page.type("#composer", "I'd like a dinner recipe. Before you suggest one, use your question tool to ask me one question about dietary restrictions.");
  await page.keyboard.press("Enter");
  await page.waitForSelector(QUESTION, { timeout: 180_000 });
  const questionThread = page.url().split("/").pop();
  const [questionRun, asking] = lastRun(questionThread);
  obs.question = await page.$eval(`${QUESTION} legend`, (l) => l.textContent.trim());
  obs.questionInput = appdb(`select kind from run_inputs where run_id = '${questionRun}'`);
  obs.questionShape = (await page.$(`${QUESTION} input[type="radio"]`)) ? "choices, with Other" : "a text field";
  if (await page.$(`${QUESTION} input[type="radio"]`)) {
    for (const pill of await page.$$(`${QUESTION} label`)) {
      if ((await pill.evaluate((l) => l.textContent.trim())) === "Other") {
        await pill.click();
        break;
      }
    }
    await page.waitForSelector(`${QUESTION} input[aria-label^="Your answer to"]`);
    await page.type(`${QUESTION} input[aria-label^="Your answer to"]`, "Vegetarian: no meat or fish.");
  } else {
    await page.type(`${QUESTION} textarea[aria-label^="Your answer to"]`, "Vegetarian: no meat or fish.");
  }
  await press(QUESTION, "Send answer");
  obs.questionEnded = await ended(questionThread);
  obs.questionEvents = appdb(`select string_agg(type, ', ' order by seq) from run_events where run_id = '${questionRun}' and type in ('input.requested', 'input.provided', 'run.completed')`);
  check(
    asking === "waiting" && obs.questionInput === "question" && obs.questionEnded === "success" && obs.questionEvents === "input.requested, input.provided, run.completed",
    "the agent's question waits for the answer, then the run goes on",
    `${obs.question}; ${obs.questionShape}; ${obs.questionEvents}`,
  );

  // 2.3d What Gen9 remembers: Settings > Memory, and a new chat that knows it
  rec.mark("2.3d memory");
  await page.goto(`${APP}/settings`, { waitUntil: "networkidle0" });
  obs.settingsMemory = await page.evaluate((c) => document.body.innerText.includes(c), colour);
  obs.switches = await page.evaluate(() =>
    ["Remember things about me", "Search and reference past chats"].map((name) => {
      const label = [...document.querySelectorAll("label")].find((l) => l.textContent.trim() === name);
      return `${name}: ${label?.closest(".border-t")?.querySelector('[role="switch"]')?.getAttribute("aria-checked")}`;
    }),
  );
  await newChat();
  // "Exactly": the colour carries this run's tag, which "the colour only" invites the model to drop
  await page.type("#composer", "What's my favourite colour? Reply with it exactly as I told you.");
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => !document.querySelector('button[aria-label="Stop"]') && document.querySelectorAll("ol[aria-live] > li").length >= 2, { timeout: 180_000, polling: 500 });
  await page.waitForNetworkIdle({ idleTime: 500 });
  const recallThread = page.url().split("/").pop();
  obs.recall = (await page.$eval("ol[aria-live] > li:last-child", (li) => li.textContent.trim())).includes(colour);
  obs.recallTools = appdb(`select count(*) from run_events e join runs r on r.id = e.run_id where r.thread_id = '${recallThread}' and e.type = 'tool.started'`);
  check(
    obs.settingsMemory && obs.switches.every((s) => s.endsWith(": true")) && obs.recall && obs.recallTools === "0",
    "Settings shows the colour and both switches on; a new chat knows it without a tool",
    `${obs.switches.join("; ")}; knows it: ${obs.recall}; tools: ${obs.recallTools}`,
  );

  // 2.3e Find an earlier chat: the agent searches past chats and cites the first one
  rec.mark("2.3e past chats");
  await newChat();
  await page.type("#composer", "In an earlier chat I asked you something about refresh tokens. Search my past chats and tell me in one sentence what I asked.");
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => !document.querySelector('button[aria-label="Stop"]') && document.querySelectorAll("ol[aria-live] > li").length >= 2, { timeout: 180_000, polling: 500 });
  await page.waitForNetworkIdle({ idleTime: 500 });
  const searchThread = page.url().split("/").pop();
  obs.pastChatSteps = appdb(`select string_agg(distinct e.data->>'name', ', ') from run_events e join runs r on r.id = e.run_id where r.thread_id = '${searchThread}' and e.type = 'tool.started'`);
  obs.pastChatSources = await page.evaluate(() => [...document.querySelectorAll("ol[aria-live] > li:last-child a")].map((a) => a.getAttribute("href")));
  obs.pastChatCites = obs.pastChatSources.some((href) => href?.endsWith(`/chat/${threadId}`)) || appdb(`select count(*) from run_events e join runs r on r.id = e.run_id where r.thread_id = '${searchThread}' and e.type = 'tool.completed' and e.data::text like '%/chat/${threadId}%'`) !== "0";
  obs.pastChatSources = obs.pastChatSources.map((href) => href?.replace(/[0-9a-f-]{36}/, "<thread id>"));
  check(
    /search_past_chats/.test(obs.pastChatSteps) && obs.pastChatCites,
    "asked about an earlier chat, the agent searches past chats and cites the first chat",
    `${obs.pastChatSteps}; cites it: ${obs.pastChatCites}`,
  );

  obs.health = {
    ui: sh("curl -s -o /dev/null -w '%{http_code}' http://localhost:14000/api/health").out,
    agentLive: sh("curl -s http://localhost:17000/healthz").out,
    agentReady: sh("curl -s http://localhost:17000/readyz").out,
    keycloak: sh("curl -s http://localhost:15001/health/ready").out.slice(0, 60),
  };

  // Silent refresh: the access token lives 5 minutes; getSession refreshes 30 s before it expires
  const before = peek(user.email);
  const lastRefresh = () => kcdb(`select max(last_session_refresh) from offline_user_session where user_id = '${user.sub}' and offline_flag = '0'`);
  const refreshedBefore = lastRefresh();
  obs.beforeRefresh = before && { accessTokenExpiresAt: before.record.accessTokenExpiresAt, authTime: before.record.authTime };
  const cookieBefore = (await page.cookies(APP)).find((c) => c.name === "gen9_session")?.value;
  if (process.env.QUICK) {
    obs.refresh = "skipped (QUICK=1)";
  } else {
    const wait = Date.parse(before.record.accessTokenExpiresAt) - Date.now() - 20_000;
    console.log(`      (waiting ${Math.round(wait / 1000)} s for the access token to near expiry)`);
    await new Promise((resolve) => setTimeout(resolve, Math.max(0, wait)));
    rec.mark("2.3 after 5 minutes");
    await page.goto(`${APP}/chat`, { waitUntil: "networkidle0" });
    const after = peek(user.email);
    const cookieAfter = (await page.cookies(APP)).find((c) => c.name === "gen9_session")?.value;
    obs.afterRefresh = after && { accessTokenExpiresAt: after.record.accessTokenExpiresAt, authTime: after.record.authTime };
    obs.refreshEvents = kcdb(`select coalesce(string_agg(type, ' ' order by event_time), '(none)') from event_entity where user_id = '${user.sub}' and event_time > ${Date.now() - 60_000}`);
    obs.lastSessionRefresh = { before: refreshedBefore, after: lastRefresh() };
    obs.sessionTtlAfter = Number(valkey(`TTL gen9:session:${sessionOf(user.sub)}`));
    check(after && after.record.accessTokenExpiresAt > before.record.accessTokenExpiresAt, "the access token was refreshed on the server", `${before.record.accessTokenExpiresAt} -> ${after?.record.accessTokenExpiresAt}`);
    check(after && after.record.authTime === before.record.authTime, "auth_time stayed the same (a refresh is not a sign-in)");
    check(cookieAfter === cookieBefore, "the browser's cookie didn't change (the refresh happened only on the server)");
    check(Number(obs.lastSessionRefresh.after) > Number(obs.lastSessionRefresh.before), "Keycloak's session row records the refresh (last_session_refresh moved)", JSON.stringify(obs.lastSessionRefresh));
    check(obs.refreshEvents === "(none)", "and its audit log doesn't: REFRESH_TOKEN isn't saved by default", obs.refreshEvents);
  }
  obs.console = rec.consoleMessages.filter((m) => m.step.startsWith("2."));
  return obs;
}
