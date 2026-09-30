// Models through the router (gen9-models), in real Chrome against the running stacks:
//   1. a chat question                 is answered through gen9-models: the router records it under
//                                      the user, and Langfuse has it under the model that answered,
//                                      priced (not the alias `chat`)
//   2. the user over their budget      the run waits for Retry after one attempt, and the card says
//                                      why and when the limit resets (Retry from the checkpoint)
//   3. the budget lifted               Retry finishes that same run
//   0. every kind, by alias            vision, embeddings, speech and its transcription, with
//                                      gen9-agent's router key
// Needs every stack up (make up) and a provider key in gen9-models/.env. Signs in as the seeded user,
// gives that user a tiny budget of their own through the router's admin API (gen9-models/.env's
// master key, never printed) and removes it again, and deletes the chat it creates.
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { deflateSync, crc32 } from "node:zlib";
import { launch } from "./browser.mjs";

const ROOT = new URL("..", import.meta.url).pathname;
const readEnv = (file) =>
  Object.fromEntries(
    (existsSync(`${ROOT}${file}`) ? readFileSync(`${ROOT}${file}`, "utf8") : "")
      .split("\n")
      .filter((line) => /^[A-Z0-9_]+=/.test(line))
      .map((line) => [line.slice(0, line.indexOf("=")), line.slice(line.indexOf("=") + 1)]),
  );
const keycloak = readEnv("gen9-keycloak/.env");
const models = readEnv("gen9-models/.env");
const agentModels = readEnv("gen9-agent/models.local.env"); // gen9-agent's own router key
const langfuse = readEnv("gen9-agent/langfuse.local.env");
const APP = process.env.APP_URL ?? "http://localhost:14000";
const ROUTER = process.env.ROUTER_URL ?? `http://127.0.0.1:${models.GEN9_MODELS_PORT || 19000}`;
const BUDGET_MESSAGE = "reached your model usage limit";
const RETRY_CARD = `section[aria-label="Gen9 couldn't finish"]`;

let failures = 0;
function check(ok, what, detail = "") {
  console.log(`${ok ? "ok  " : "FAIL"}  ${what}${detail ? `: ${detail}` : ""}`);
  if (!ok) failures++;
  return ok;
}
const psql = (container, user, db, sql) =>
  execFileSync("docker", ["exec", container, "psql", "-U", user, "-d", db, "-tAc", sql]).toString().trim();
const sub = psql("gen9-keycloak-postgres-1", "keycloak", "keycloak",
  `select id from user_entity where email = '${keycloak.GEN9_SEED_USER_EMAIL}' and realm_id = (select id from realm where name = 'gen9')`);
const router = (path, body) =>
  fetch(`${ROUTER}${path}`, {
    method: "POST",
    headers: { authorization: `Bearer ${models.LITELLM_MASTER_KEY}`, "content-type": "application/json" },
    body: JSON.stringify(body),
  });
// Each kind of model by its alias, as gen9-agent calls the router (its key, never a vendor name)
const asAgent = (path, init = {}) =>
  fetch(`${ROUTER}${path}`, { method: "POST", ...init, headers: { authorization: `Bearer ${agentModels.GEN9_MODELS_KEY}`, ...(init.headers ?? {}) } });
// A red square on white, 256 px, as a PNG built here. The question says which square: asked for
// "the square in the middle", GPT-5.4 nano said "White" (the whole image) 2 times in 3, and "Red"
// 16 times in 16 once told the square is on a white background (docs/plans/harness.md, Surprises)
function redPng(size = 256) {
  const chunk = (type, data) => {
    const body = Buffer.concat([Buffer.from(type), data]);
    const length = Buffer.alloc(4); length.writeUInt32BE(data.length);
    const crc = Buffer.alloc(4); crc.writeUInt32BE(crc32(body));
    return Buffer.concat([length, body, crc]);
  };
  const header = Buffer.alloc(13);
  header.writeUInt32BE(size, 0); header.writeUInt32BE(size, 4); header.writeUInt8(8, 8); header.writeUInt8(2, 9);
  const red = (x, y) => x >= size / 4 && x < (size * 3) / 4 && y >= size / 4 && y < (size * 3) / 4;
  const rows = Array.from({ length: size }, (_, y) =>
    Buffer.from([0, ...Array.from({ length: size }, (_, x) => (red(x, y) ? [255, 0, 0] : [255, 255, 255])).flat()]),
  );
  const png = Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk("IHDR", header), chunk("IDAT", deflateSync(Buffer.concat(rows))), chunk("IEND", Buffer.alloc(0))]);
  return `data:image/png;base64,${png.toString("base64")}`;
}
async function everyKind() {
  const json = { "content-type": "application/json" };
  const vision = await asAgent("/v1/chat/completions", { headers: json, body: JSON.stringify({ model: "vision", messages: [{ role: "user", content: [{ type: "text", text: "The image shows a coloured square on a white background. What colour is that square? One word." }, { type: "image_url", image_url: { url: redPng() } }] }] }) }).then((r) => r.json());
  const colour = vision.choices?.[0]?.message?.content ?? JSON.stringify(vision).slice(0, 120);
  check(/red/i.test(colour), "vision by alias: an image as input", colour);
  const embedded = await asAgent("/v1/embeddings", { headers: json, body: JSON.stringify({ model: "embed", input: ["Gen9 routes every kind of model."] }) }).then((r) => r.json());
  const dimensions = embedded.data?.[0]?.embedding?.length;
  check(dimensions === 1024, "embeddings by alias, at the dimensions config.yaml sets", `${dimensions} dimensions`);
  const speech = await asAgent("/v1/audio/speech", { headers: json, body: JSON.stringify({ model: "speak", voice: "alloy", input: "Gen9 routes every kind of model." }) });
  const audio = Buffer.from(await speech.arrayBuffer());
  const form = new FormData();
  form.append("model", "transcribe");
  form.append("response_format", "json"); // the model refuses LiteLLM's default, verbose_json
  form.append("file", new Blob([audio], { type: "audio/mpeg" }), "speech.mp3");
  const heard = await asAgent("/v1/audio/transcriptions", { body: form }).then((r) => r.json());
  check(speech.ok && /every kind of model/i.test(heard.text ?? ""), "speech, then its transcription, by alias", `${audio.length} bytes -> "${heard.text ?? JSON.stringify(heard).slice(0, 100)}"`);
}

const latestRun = (threadId) =>
  psql("gen9-postgres-postgres-1", "postgres", "gen9_agent",
    `select status || '|' || attempts from runs where thread_id = '${threadId}' order by created_at desc limit 1`);

const browser = await launch({ headless: !process.env.HEADED, defaultViewport: { width: 1280, height: 900 } });
const page = await browser.newPage();
const navigation = (action) => Promise.all([page.waitForNavigation({ waitUntil: "networkidle0", timeout: 20_000 }), action]);
const lastAnswer = () => page.$$eval("ol[aria-live] > li", (lis) => lis.at(-1)?.textContent.trim() ?? "");
// Send a message and wait until the page has stopped answering
async function send(text) {
  await page.type("#composer", text);
  await page.click('button[aria-label="Send"]');
  await page.waitForSelector('button[aria-label="Stop"]', { timeout: 20_000 }).catch(() => {});
  await page.waitForFunction(() => !document.querySelector('button[aria-label="Stop"]'), { timeout: 180_000, polling: 500 });
}

let threadId;
let tinyBudget = null;
try {
  // 0. Every kind of model the router serves, by alias
  await everyKind();

  await page.goto(`${APP}/auth/login`, { waitUntil: "networkidle0" });
  await page.type("#username", keycloak.GEN9_SEED_USER_EMAIL);
  await page.type("#password", keycloak.GEN9_SEED_USER_PASSWORD);
  await navigation(page.click("#kc-login"));
  if (!check(page.url().startsWith(`${APP}/chat`), "signed in as the seeded user", page.url())) throw new Error("cannot continue");

  // 1. Answered through the router
  const startedAt = new Date();
  await page.goto(`${APP}/chat`, { waitUntil: "networkidle0" });
  await send("Reply with one word: routed");
  threadId = page.url().split("/").pop();
  const answer = await lastAnswer();
  // The router writes its spend log in batches, a few seconds after each call
  const spentSince = () => psql("gen9-models-postgres-1", "litellm", "litellm",
    `select count(*) || ' calls, models ' || coalesce(string_agg(distinct model, ','), '-') from "LiteLLM_SpendLogs" where end_user = '${sub}' and "startTime" >= '${startedAt.toISOString()}'`);
  let spent = spentSince();
  for (let i = 0; i < 30 && spent.startsWith("0 "); i++) {
    await new Promise((resolve) => setTimeout(resolve, 2000));
    spent = spentSince();
  }
  check(/routed/i.test(answer) && !spent.startsWith("0 "), "the answer came through gen9-models, recorded under the user", `"${answer}"; router: ${spent}`);
  // Langfuse's v2 API puts the user on the run's root observation, not its generations: find the
  // user's traces since the start, then their generations (as gen9-agent's erasure queries)
  const observations = async (params) => {
    const response = await fetch(`${langfuse.LANGFUSE_BASE_URL}/api/public/v2/observations?${new URLSearchParams(params)}`, {
      headers: { authorization: `Basic ${Buffer.from(`${langfuse.LANGFUSE_PUBLIC_KEY}:${langfuse.LANGFUSE_SECRET_KEY}`).toString("base64")}` },
    });
    return (await response.json()).data ?? [];
  };
  let generation;
  for (let i = 0; i < 30 && !generation; i++) {
    const since = startedAt.toISOString();
    const traces = new Set((await observations({ userId: sub, fromStartTime: since, fields: "core" })).map((o) => o.traceId));
    const generations = await observations({ type: "GENERATION", fromStartTime: since, fields: "core,basic,model,usage" });
    generation = generations.find((o) => traces.has(o.traceId));
    if (!generation) await new Promise((resolve) => setTimeout(resolve, 2000));
  }
  const cost = generation?.totalCost ?? generation?.costDetails?.total;
  check(generation && generation.model !== "chat" && cost > 0, "Langfuse has the call under the model that answered, priced",
    generation ? `${generation.model}, $${cost}` : "no generation within 60 s");

  // 2. Over budget
  // A budget of its own with a period, so it has a time to reset at, which the card names
  // (P5-D1): the router's /customer/update takes a limit but no period
  tinyBudget = `gen9-e2e-tiny-${Date.now()}`;
  await router("/budget/new", { budget_id: tinyBudget, max_budget: 0.0000001, budget_duration: "30d" });
  const set = await router("/customer/update", { user_id: sub, budget_id: tinyBudget });
  if (!set.ok) await router("/customer/new", { user_id: sub, budget_id: tinyBudget });
  await page.type("#composer", "Reply with one word: over");
  await page.click('button[aria-label="Send"]');
  const card = await page.waitForSelector(RETRY_CARD, { timeout: 60_000 }).then((c) => c.evaluate((e) => e.textContent)).catch(() => null);
  const refused = latestRun(threadId);
  check(
    card?.includes(BUDGET_MESSAGE) && /It resets on \d{1,2} \w+ \d{4} at \d{2}:\d{2} UTC/.test(card) && refused === "waiting|1",
    "over their budget, the run waits for Retry after one attempt, and says why and when the limit resets",
    `"${card?.slice(0, 160)}"; run ${refused}`,
  );
} catch (error) {
  check(false, "unexpected error", error.message.split("\n")[0]);
} finally {
  // 3. The budget lifted: the user's own row goes (spend history stays in the router's logs), and
  // so does the tiny budget made for it, which the router would otherwise keep for good
  await router("/customer/delete", { user_ids: [sub] }).catch(() => {});
  if (tinyBudget) await router("/budget/delete", { id: tinyBudget }).catch(() => {});
  if (threadId) {
    await page.goto(`${APP}/chat/${threadId}`, { waitUntil: "load" }).catch(() => {});
    const waiting = psql("gen9-postgres-postgres-1", "postgres", "gen9_agent", `select id from runs where thread_id = '${threadId}' and status = 'waiting' limit 1`);
    const retry = await page.waitForSelector(`${RETRY_CARD} button`, { timeout: 10_000 }).catch(() => null);
    for (const b of await page.$$(`${RETRY_CARD} button`)) if ((await b.evaluate((e) => e.textContent.trim())) === "Retry") await b.click();
    if (retry) await page.waitForFunction(() => !document.querySelector('button[aria-label="Stop"]'), { timeout: 180_000, polling: 500 }).catch(() => {});
    else await send("Reply with one word: again").catch(() => {});
    const same = waiting && psql("gen9-postgres-postgres-1", "postgres", "gen9_agent", `select status from runs where id = '${waiting}'`);
    check(same === "success" && latestRun(threadId).startsWith("success|"), "with the budget lifted, Retry finishes that same run", `${waiting}: ${same}; latest ${latestRun(threadId)}`);
    // Delete the chat the way a user does
    await page.click('button[aria-label="Chat options"]').catch(() => {});
    // By its name: the menu has other items (Rename comes first)
    await page.waitForSelector("[role=menuitem]", { timeout: 5000 }).catch(() => null);
    let item = null;
    for (const el of await page.$$("[role=menuitem]")) if ((await el.evaluate((e) => e.textContent.trim())) === "Delete chat") item = el;
    if (item) {
      await item.click();
      const confirm = await page.waitForSelector('[role="alertdialog"] button:last-of-type', { timeout: 5000 }).catch(() => null);
      if (confirm) await navigation(confirm.click()).catch(() => {});
    }
    check(latestRun(threadId) === "", "the chat is deleted", threadId);
  }
  await browser.close();
}
console.log(failures ? `\n${failures} check(s) failed` : "\nall checks passed");
process.exit(failures ? 1 : 0);
