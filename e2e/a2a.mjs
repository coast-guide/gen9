// Gen9 as an A2A agent (gen9-agent/README.md, "A2A"), as another agent uses it: it reads Gen9's Agent
// Card, signs the seeded user in with the OAuth flow the card declares (authorization code with
// PKCE at Gen9's Keycloak, the pre-registered public client, scope gen9-a2a; Puppeteer types the
// password and consents), then drives tasks with the official JS client (@a2a-js/sdk 1.2.1,
// A2A 1.0 over JSON-RPC). As the seeded user:
//   1. the Agent Card: the endpoint, streaming, and Keycloak's flow; no token: 401
//   2. SendMessage completes a task whose artifact holds an unguessable phrase; a second message
//      in the same context remembers it; GetTask and ListTasks see the task
//   3. streaming: the task, then artifact chunks that add up to the answer, then completed
//   4. "Ask before acting" (the message's metadata) ends input-required, saying what it asks and
//      the answer's schema; a reply on the task with Gen9's answer (approve) completes it, and
//      the memory has it (then put back)
//   5. CancelTask stops a long task; a token for another audience is refused
//   6. the agent sees only the chats it started (its consent: "send it tasks and read their
//      results"): a chat of the person's own isn't listed, read, continued, switched to "auto" or
//      cancelled through it; and a message sent twice (the same messageId) is one task
// It deletes its chats at the end, and costs a few short replies and one essay stopped early.
import { createHash, randomBytes, randomUUID } from "node:crypto";
import { createServer } from "node:http";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawn } from "node:child_process";
import { ClientFactory, JsonRpcTransportFactory } from "@a2a-js/sdk/client";
import { launch } from "./browser.mjs";
import { ROOT, signInTerminal } from "./signin.mjs";

const env = Object.fromEntries(
  readFileSync(`${ROOT}gen9-keycloak/.env`, "utf8")
    .split("\n")
    .filter((line) => /^[A-Z0-9_]+=/.test(line))
    .map((line) => [line.slice(0, line.indexOf("=")), line.slice(line.indexOf("=") + 1)]),
);
const API = process.env.GEN9_API ?? "http://localhost:17000";
const KEYCLOAK = process.env.KEYCLOAK_URL ?? "http://localhost:15000";
const PHRASE = `tern-${randomBytes(3).toString("hex")}`;
const BIRD = `plover-${randomBytes(3).toString("hex")}`;
// A2A 1.0's enums as the JS SDK has them
const ROLE_USER = 1;
const COMPLETED = 3;
const CANCELED = 5;
const INPUT_REQUIRED = 6;

let failures = 0;
function check(ok, what, detail = "") {
  console.log(`${ok ? "ok  " : "FAIL"}  ${what}${detail ? `: ${detail}` : ""}`);
  if (!ok) failures++;
  return ok;
}
const gen9 = (configDir, args) =>
  new Promise((resolve) => {
    const child = spawn("uv", ["run", "-q", "gen9", ...args], { cwd: `${ROOT}gen9-cli`, env: { ...process.env, GEN9_CONFIG_DIR: configDir } });
    child.on("exit", (code) => resolve(code));
    child.stdin.end();
  });
async function cliToken(configDir) {
  await gen9(configDir, ["whoami"]);
  return JSON.parse(readFileSync(join(configDir, "credentials.json"), "utf8")).access_token;
}
async function admin(path, init = {}) {
  const token = await fetch(`${KEYCLOAK}/realms/master/protocol/openid-connect/token`, {
    method: "POST",
    body: new URLSearchParams({ grant_type: "password", client_id: "admin-cli", username: env.KC_BOOTSTRAP_ADMIN_USERNAME, password: env.KC_BOOTSTRAP_ADMIN_PASSWORD }),
  })
    .then((r) => r.json())
    .then((body) => body.access_token);
  const response = await fetch(`${KEYCLOAK}/admin/realms/gen9${path}`, { ...init, headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" } });
  return response.status === 200 ? response.json() : null;
}
async function revokeConsent() {
  const [user] = (await admin(`/users?email=${encodeURIComponent(env.GEN9_SEED_USER_EMAIL)}&exact=true`)) ?? [];
  if (user) await admin(`/users/${user.id}/consents/gen9-mcp`, { method: "DELETE" });
}
const claims = (jwt) => JSON.parse(Buffer.from(jwt.split(".")[1], "base64url").toString());
const text = (value) => ({ content: { $case: "text", value } });
const data = (value) => ({ content: { $case: "data", value } });
const message = (parts, extra = {}) => ({ messageId: randomUUID(), role: ROLE_USER, parts, contextId: "", taskId: "", metadata: undefined, extensions: [], referenceTaskIds: [], ...extra });
const send = (client, msg, configuration = undefined) => client.sendMessage({ tenant: "", message: msg, configuration, metadata: undefined });
const answerOf = (task) => (task.artifacts ?? []).flatMap((a) => a.parts).map((p) => (p.content?.$case === "text" ? p.content.value : "")).join("");

const browser = await launch({ headless: !process.env.HEADED, defaultViewport: { width: 1280, height: 900 } });
const page = await browser.newPage();
const callback = createServer();
const alan = mkdtempSync(join(tmpdir(), "gen9-a2a-alan-"));
const contexts = [];
let memoryBefore = null;
try {
  await revokeConsent();
  check(Boolean(await signInTerminal({ email: env.GEN9_SEED_USER_EMAIL, password: env.GEN9_SEED_USER_PASSWORD, configDir: alan })), "the seeded user signs in on the terminal");

  // 1. The Agent Card, and the sign-in it declares
  const card = await (await fetch(`${API}/.well-known/agent-card.json`)).json();
  const flow = card.securitySchemes?.keycloak?.oauth2SecurityScheme?.flows?.authorizationCode;
  check(card.supportedInterfaces?.[0]?.url === `${API}/a2a` && card.supportedInterfaces[0].protocolBinding === "JSONRPC" && card.capabilities?.streaming === true && flow?.pkceRequired === true && "gen9-a2a" in (flow?.scopes ?? {}), "the Agent Card names the endpoint, streaming, and Keycloak's flow with PKCE and the scope");
  const bare = await fetch(`${API}/a2a`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  check(bare.status === 401, "without a token: 401", `${bare.status} ${bare.headers.get("www-authenticate")}`);

  await new Promise((resolve) => callback.listen(0, "127.0.0.1", resolve));
  const redirect = `http://127.0.0.1:${callback.address().port}/callback`;
  const code = new Promise((resolve) => callback.on("request", (req, res) => (resolve(new URL(req.url, redirect).searchParams.get("code")), res.end("ok"))));
  const verifier = randomBytes(32).toString("base64url");
  const auth = new URL(flow.authorizationUrl);
  for (const [k, v] of Object.entries({ client_id: "gen9-mcp", response_type: "code", redirect_uri: redirect, scope: "openid gen9-a2a", code_challenge: createHash("sha256").update(verifier).digest("base64url"), code_challenge_method: "S256", state: randomUUID() })) auth.searchParams.set(k, v);
  await page.goto(auth.toString(), { waitUntil: "networkidle0" });
  await page.type("#username", env.GEN9_SEED_USER_EMAIL);
  await page.type("#password", env.GEN9_SEED_USER_PASSWORD);
  await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }).catch(() => {}), page.click("#kc-login")]);
  const consent = (await page.$('button[name="accept"]')) ? await page.$eval("main, body", (m) => m.textContent) : "";
  if (consent) await Promise.all([page.waitForNavigation().catch(() => {}), page.click('button[name="accept"]')]);
  const tokens = await (await fetch(flow.tokenUrl, { method: "POST", body: new URLSearchParams({ grant_type: "authorization_code", code: await code, redirect_uri: redirect, client_id: "gen9-mcp", code_verifier: verifier }) })).json();
  const token = tokens.access_token ?? "";
  check(/work with Gen9 for you/.test(consent) && [claims(token).aud].flat().includes(`${API}/a2a`) && /gen9-a2a/.test(claims(token).scope), "signed in by the card's flow: consent says what it allows, and the token is for the A2A endpoint", [claims(token).aud].flat().join(", "));
  const signed = (bearer) => (input, init = {}) => fetch(input, { ...init, headers: { ...Object.fromEntries(new Headers(init.headers ?? {})), Authorization: `Bearer ${bearer}` } });
  const client = await new ClientFactory({ transports: [new JsonRpcTransportFactory({ fetchImpl: signed(token) })] }).createFromUrl(API);

  // 2. A task, a second one in its context, and reading them
  const first = await send(client, message([text(`Reply with exactly this and nothing else: ${PHRASE}`)]));
  contexts.push(first.contextId);
  check(first.status?.state === COMPLETED && answerOf(first).includes(PHRASE), "SendMessage completes a task whose artifact holds the answer", answerOf(first).slice(0, 40));
  const second = await send(client, message([text("What exactly did I ask you to reply with? Reply with that word only.")], { contextId: first.contextId }));
  check(second.contextId === first.contextId && second.id !== first.id && answerOf(second).includes(PHRASE), "a second message in the same context is a new task that remembers the first", answerOf(second).slice(0, 40));
  const got = await client.getTask({ tenant: "", id: first.id, historyLength: undefined });
  const listed = await client.listTasks({ tenant: "", contextId: first.contextId, status: 0, pageSize: 10, pageToken: "", historyLength: undefined, statusTimestampAfter: undefined, includeArtifacts: undefined });
  check(got.status?.state === COMPLETED && got.history?.length === 2 && listed.tasks?.map((t) => t.id).sort().join() === [first.id, second.id].sort().join(), "GetTask has its question and answer; ListTasks has both tasks of the context", `${got.history?.length} history; ${listed.tasks?.length} listed`);

  // 3. Streaming
  let chunks = "";
  const seen = [];
  for await (const update of client.sendMessageStream({ tenant: "", message: message([text(`Reply with exactly: ${PHRASE}-streamed`)], { contextId: first.contextId }), configuration: undefined, metadata: undefined })) {
    const payload = update.payload ?? update;
    const kind = payload.$case ?? (update.kind ?? "task");
    seen.push(kind);
    if (kind === "artifactUpdate") chunks += (payload.value.artifact?.parts ?? []).map((p) => (p.content?.$case === "text" ? p.content.value : "")).join("");
    if (kind === "statusUpdate" && payload.value.status?.state === COMPLETED) seen.push("completed");
  }
  check(seen[0] === "task" && seen.includes("artifactUpdate") && seen.at(-1) === "completed" && chunks.includes(`${PHRASE}-streamed`), "streaming sends the task, artifact chunks that make the answer, then completed", [...new Set(seen)].join(", "));

  // 3b. ListTasks in pages (A2A 1.0.1: newest change first, a cursor, nextPageToken always there
  // and empty on the last page, no artifacts unless asked); it once gave only the first page
  const pages = [];
  let cursor = "";
  do {
    const page = await client.listTasks({ tenant: "", contextId: first.contextId, status: 0, pageSize: 1, pageToken: cursor, historyLength: undefined, statusTimestampAfter: undefined, includeArtifacts: undefined });
    pages.push(page);
    cursor = page.nextPageToken;
  } while (cursor && pages.length < 10);
  const paged = pages.flatMap((p) => p.tasks.map((t) => t.id));
  // A page token is the base64 of a task id, so anyone can forge one: Gen9 takes only one naming
  // the caller's own task, and says the same of any other (no telling whose exists); a page over
  // its maximum is refused too (P4-D3)
  const listing = async (params) => {
    try {
      await client.listTasks({ tenant: "", contextId: "", status: 0, pageSize: 1, pageToken: "", historyLength: undefined, statusTimestampAfter: undefined, includeArtifacts: undefined, ...params });
      return "listed";
    } catch (e) {
      return String(e.message ?? e).slice(0, 70);
    }
  };
  const forged = await Promise.all([
    listing({ pageToken: Buffer.from(randomUUID()).toString("base64") }),
    listing({ pageToken: "not a token" }),
    listing({ pageSize: 1000 }),
  ]);
  const withArtifacts = await client.listTasks({ tenant: "", contextId: first.contextId, status: 0, pageSize: 1, pageToken: "", historyLength: undefined, statusTimestampAfter: undefined, includeArtifacts: true });
  check(pages.length === 3 && pages.every((p) => p.tasks.length === 1 && p.totalSize === 3) && pages.at(-1).nextPageToken === "" && paged[2] === first.id && new Set(paged).size === 3 && pages.every((p) => !p.tasks[0].artifacts?.length) && withArtifacts.tasks[0].artifacts?.length === 1, "ListTasks pages through the context's three tasks one by one, newest change first, the token empty at the end, artifacts only when asked", `${pages.length} page(s): ${paged.map((id) => (id === first.id ? "first" : id === second.id ? "second" : "streamed")).join(", ")}; last token "${pages.at(-1).nextPageToken}"`);
  // (a token that isn't base64 at all the A2A SDK refuses itself, in its own words)
  check(/isn't one Gen9 gave/.test(forged[0]) && forged[1] !== "listed" && /pageSize/.test(forged[2]), "ListTasks refuses a page token naming a task that isn't the caller's, and a page over its maximum", forged.join(" | "));

  // 3c. A stream dropped mid-answer is taken up again (SubscribeToTask), and an ended task refuses
  let dropped = null;
  for await (const update of client.sendMessageStream({ tenant: "", message: message([text("Count from 1 to 60, one number per line, nothing else.")], { contextId: first.contextId }), configuration: undefined, metadata: undefined })) {
    dropped = (update.payload ?? update).value?.id ?? (update.payload ?? update).value?.taskId ?? dropped;
    if (dropped) break; // the client goes away after the task's first event
  }
  let again = "";
  const resumedKinds = [];
  for await (const update of client.resubscribeTask({ tenant: "", id: dropped })) {
    const payload = update.payload ?? update;
    resumedKinds.push(payload.$case);
    if (payload.$case === "artifactUpdate") again += (payload.value.artifact?.parts ?? []).map((p) => (p.content?.$case === "text" ? p.content.value : "")).join("");
    if (payload.$case === "statusUpdate" && payload.value.status?.state === COMPLETED) resumedKinds.push("completed");
  }
  let ended = null;
  try {
    for await (const _ of client.resubscribeTask({ tenant: "", id: first.id })) break;
  } catch (e) {
    ended = e;
  }
  check(resumedKinds[0] === "task" && resumedKinds.at(-1) === "completed" && /\b60\b/.test(again) && /ended|Unsupported/i.test(String(ended?.message ?? ended)), "a dropped stream is taken up with SubscribeToTask to the end; an ended task refuses it", `${[...new Set(resumedKinds)].join(", ")}; ${String(ended?.message ?? ended).slice(0, 60)}`);

  // 3d. Push notifications: the card says none, and each method refuses (raw JSON-RPC: a client
  // may refuse by itself from the card)
  const rpc = async (method, params) => (await (await fetch(`${API}/a2a`, { method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", "A2A-Version": "1.0" }, body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }) })).json());
  const pushes = [await rpc("CreateTaskPushNotificationConfig", { taskId: first.id, id: "p1", url: "https://example.com/hook" }), await rpc("GetTaskPushNotificationConfig", { taskId: first.id, id: "p1" })];
  check(pushes.every((r) => r.error?.code === -32003), "push notification configs are refused (PushNotificationNotSupported, -32003), as the card says", pushes.map((r) => `${r.error?.code} ${r.error?.message ?? ""}`.trim()).join("; "));

  // 4. Input required, and the reply that answers it
  memoryBefore = (await (await fetch(`${API}/v1/me/memory`, { headers: { Authorization: `Bearer ${await cliToken(alan)}` } })).json()).content ?? "";
  let asking = await send(client, message([text(`Please remember that my favourite bird is the ${BIRD}. Reply with exactly: Noted.`)], { metadata: { permissionMode: "ask" } }));
  contexts.push(asking.contextId);
  const said = asking.status?.message?.parts ?? [];
  const asked = said.find((p) => p.content?.$case === "data")?.content.value?.gen9?.[0];
  check(asking.status?.state === INPUT_REQUIRED && /Gen9 needs you/.test(said.find((p) => p.content?.$case === "text")?.content.value ?? "") && asked?.kind === "approval" && asked.answer_schema?.required?.[0] === "decisions", "Ask before acting ends input-required, saying what it asks and the answer's schema", asked?.kind);
  // As a client does: each approval it's shown gets an answer until the task ends (a turn may
  // write memory more than once)
  for (let round = 0; round < 3 && asking.status?.state === INPUT_REQUIRED; round++) {
    asking = await send(client, message([data({ decisions: [{ type: "approve" }] })], { taskId: asking.id, contextId: asking.contextId }));
  }
  const memory = (await (await fetch(`${API}/v1/me/memory`, { headers: { Authorization: `Bearer ${await cliToken(alan)}` } })).json()).content ?? "";
  check(asking.status?.state === COMPLETED && memory.includes(BIRD), "a reply on the task with Gen9's answer (approve) completes it, and the memory has it", String(asking.status?.state));

  // 5. Cancel, and another audience's token
  const long = await send(client, message([text("Write a detailed 2000-word essay on the history of the compass, with sections.")], { contextId: first.contextId }), { acceptedOutputModes: [], taskPushNotificationConfig: undefined, historyLength: undefined, returnImmediately: true });
  await new Promise((r) => setTimeout(r, 4000));
  const cancelled = await client.cancelTask({ tenant: "", id: long.id, metadata: undefined });
  check(cancelled.status?.state === CANCELED, "CancelTask stops a long task", String(cancelled.status?.state));
  const wrong = await fetch(`${API}/a2a`, { method: "POST", headers: { Authorization: `Bearer ${await cliToken(alan)}`, "Content-Type": "application/json", "A2A-Version": "1.0" }, body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "ListTasks", params: {} }) });
  check(wrong.status === 401, "a token for Gen9's API (another audience) is refused", `${wrong.status}`);

  // 6. Only the chats it started; one task for a message sent twice (manual-e2e.md, P5-C7)
  const own = async (method, path, body) =>
    (await fetch(`${API}${path}`, { method, headers: { Authorization: `Bearer ${await cliToken(alan)}`, "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined })).json();
  const ownChat = await own("POST", "/v1/threads");
  contexts.push(ownChat.id);
  const ownRun = await own("POST", `/v1/threads/${ownChat.id}/runs`, { message: "Reply with exactly: MINE", permission_mode: "ask" });
  for (let i = 0; i < 60 && (await own("GET", `/v1/threads/${ownChat.id}/runs/${ownRun.id}`)).status !== "success"; i++) await new Promise((r) => setTimeout(r, 1000));
  const outcome = (call) => call.then(() => "answered", (e) => `refused (${String(e?.message ?? e).slice(0, 40)})`);
  const listedAll = await client.listTasks({ tenant: "", contextId: "", status: 0, pageSize: 100, pageToken: "", historyLength: undefined, statusTimestampAfter: undefined, includeArtifacts: undefined });
  const reads = await outcome(client.getTask({ tenant: "", id: ownRun.id, historyLength: undefined }));
  const continues = await outcome(send(client, message([text("Reply with exactly: TAKEN")], { contextId: ownChat.id, metadata: { permissionMode: "auto" } })));
  const cancels = await outcome(client.cancelTask({ tenant: "", id: ownRun.id, metadata: undefined }));
  const modeAfter = (await own("GET", `/v1/threads/${ownChat.id}`)).permission_mode;
  check(
    !(listedAll.tasks ?? []).some((t) => t.contextId === ownChat.id) && reads !== "answered" && continues !== "answered" && cancels !== "answered" && modeAfter === "ask",
    "the agent sees only the chats it started: a chat of the person's own isn't listed, read, continued, switched to auto or cancelled",
    `listed ${(listedAll.tasks ?? []).some((t) => t.contextId === ownChat.id)}; get ${reads}; continue ${continues}; cancel ${cancels}; mode ${modeAfter}`,
  );
  const once = message([text("Reply with exactly: ONCE")]);
  const sentOnce = await send(client, once);
  contexts.push(sentOnce.contextId);
  const sentTwice = await send(client, once).catch((e) => ({ id: `refused: ${e.message}` }));
  check(sentTwice.id === sentOnce.id, "the same message sent twice (its messageId) is one task", `${sentOnce.id} / ${sentTwice.id}`);
} catch (e) {
  check(false, "the run finished", e.stack ?? String(e));
} finally {
  const apiToken = await cliToken(alan).catch(() => null);
  if (memoryBefore !== null) await fetch(`${API}/v1/me/memory`, { method: "PUT", headers: { Authorization: `Bearer ${apiToken}`, "Content-Type": "application/json" }, body: JSON.stringify({ content: memoryBefore }) }).catch(() => {});
  for (const id of contexts.filter(Boolean)) await fetch(`${API}/v1/threads/${id}`, { method: "DELETE", headers: { Authorization: `Bearer ${apiToken}` } }).catch(() => {});
  await revokeConsent().catch(() => {});
  await browser.close();
  callback.close();
  rmSync(alan, { recursive: true, force: true });
}
console.log(failures ? `\n${failures} check(s) failed` : "\nall A2A checks passed");
process.exit(failures ? 1 : 0);
