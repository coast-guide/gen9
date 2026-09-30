// Gen9 as an MCP server (gen9-agent/README.md, "MCP server"), used as MCP clients use it: the
// official TypeScript SDK (@modelcontextprotocol/sdk, protocol 2025-11-25 with its handshake)
// finds Keycloak from the server's metadata, signs the seeded user in with the pre-registered
// client `gen9-mcp` (authorization code, PKCE, a loopback redirect; Puppeteer types the password
// and gives consent), then calls the tools. As the seeded user:
//   1. without a token: 401 naming the metadata and the scope; the metadata names Keycloak
//   2. OAuth: the consent screen names what it allows; the token's audience is the server
//   3. tools: ask (a new chat, then a follow-up in it by its id), read_chat, list_chats,
//      search_chats (one hit per chat); every result structured, with the chat's id
//   4. refused: the API's own token on /mcp, the MCP token on the API, another person's chat
//   5. FastMCP's Python client (protocol 2026-07-28, stateless) lists the chats with the token
//   6. MCP Tasks: the v2 client with @modelcontextprotocol/ext-tasks gets `ask` as a task that
//      completes, answers a run's question through tasks/update, cancels one; another person's
//      run and a made-up id are not found; tasks/get without the extension gets -32021
//   7. a client identified by its Client ID Metadata Document (served here) signs in the same way,
//      and its token works on the tools
//   8. Settings, Apps with access, lists both with what they may do, and Remove access signs each
//      out: gone from the list, its refresh token refused
// It deletes its chats at the end, and costs five short replies.
import { execFileSync, spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { createServer } from "node:http";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { UnauthorizedError } from "@modelcontextprotocol/sdk/client/auth.js";
import { Client as ClientV2, StreamableHTTPClientTransport as HTTPV2 } from "@modelcontextprotocol/client";
import { createApplicationInputHandler, createTaskSessionFromClient, resultFromTaskOutcome } from "@modelcontextprotocol/ext-tasks/client";
import { launch } from "./browser.mjs";
import { ROOT, signInTerminal } from "./signin.mjs";

const env = Object.fromEntries(
  readFileSync(`${ROOT}gen9-keycloak/.env`, "utf8")
    .split("\n")
    .filter((line) => /^[A-Z0-9_]+=/.test(line))
    .map((line) => [line.slice(0, line.indexOf("=")), line.slice(line.indexOf("=") + 1)]),
);
const API = process.env.GEN9_API ?? "http://localhost:17000";
const MCP = `${API}/mcp`;
// gen9-postgres as the superuser (ids and statuses only)
const psql = (query) => execFileSync("docker", ["exec", "gen9-postgres-postgres-1", "psql", "-U", "postgres", "-d", "gen9_agent", "-tAc", query], { encoding: "utf8" }).trim();
const PHRASE = `quokka-${randomBytes(3).toString("hex")}`;

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
const KEYCLOAK = process.env.KEYCLOAK_URL ?? "http://localhost:15000";
// Keycloak's admin API as its bootstrap admin (gen9-keycloak/.env), as approvals.mjs does
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
// The seeded user's consent to the MCP client, revoked so the consent screen shows each run
async function revokeConsent() {
  const [user] = (await admin(`/users?email=${encodeURIComponent(env.GEN9_SEED_USER_EMAIL)}&exact=true`)) ?? [];
  if (user) await admin(`/users/${user.id}/consents/gen9-mcp`, { method: "DELETE" });
}
const claims = (jwt) => JSON.parse(Buffer.from(jwt.split(".")[1], "base64url").toString());

const browser = await launch({ headless: !process.env.HEADED, defaultViewport: { width: 1280, height: 900 } });
const page = await browser.newPage();
const servers = [];

// An MCP client as the SDK drives it: its loopback redirect (RFC 8252) with the code it receives,
// what it keeps (its client id, PKCE verifier, tokens), and the person's browser signing in and
// consenting. `clientId`: the pre-registered client, or a Client ID Metadata Document's URL
async function oauthClient(clientId, name) {
  const callback = createServer();
  await new Promise((resolve) => callback.listen(0, "127.0.0.1", resolve));
  servers.push(callback);
  const redirect = `http://127.0.0.1:${callback.address().port}/callback`;
  let gotCode;
  const code = new Promise((resolve) => (gotCode = resolve));
  callback.on("request", (req, res) => {
    gotCode(new URL(req.url, redirect).searchParams.get("code"));
    res.end("Signed in. You can close this tab.");
  });
  const saved = { consent: "" };
  const provider = {
    get redirectUrl() {
      return redirect;
    },
    get clientMetadata() {
      return { client_name: name, redirect_uris: [redirect], grant_types: ["authorization_code", "refresh_token"], response_types: ["code"], token_endpoint_auth_method: "none" };
    },
    clientInformation: () => ({ client_id: typeof clientId === "function" ? clientId(redirect) : clientId }),
    tokens: () => saved.tokens,
    saveTokens: (tokens) => void (saved.tokens = tokens),
    saveCodeVerifier: (verifier) => void (saved.verifier = verifier),
    codeVerifier: () => saved.verifier,
    async redirectToAuthorization(url) {
      saved.authorizationUrl = url.toString();
      await page.goto(url.toString(), { waitUntil: "networkidle0" });
      // Signed in already (Keycloak's session from an earlier client): straight to consent
      if (await page.$("#username")) {
        await page.type("#username", env.GEN9_SEED_USER_EMAIL);
        await page.type("#password", env.GEN9_SEED_USER_PASSWORD);
        await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }).catch(() => {}), page.click("#kc-login")]);
      }
      if (await page.$('button[name="accept"]')) {
        saved.consent = await page.$eval("main, body", (m) => m.textContent);
        await Promise.all([page.waitForNavigation().catch(() => {}), page.click('button[name="accept"]')]);
      }
    },
  };
  const connect = async () => {
    const client = new Client({ name: "gen9-e2e", version: "1.0.0" });
    await client.connect(new StreamableHTTPClientTransport(new URL(MCP), { authProvider: provider }));
    return client;
  };
  let client;
  try {
    client = await connect();
  } catch (e) {
    if (!(e instanceof UnauthorizedError)) throw e;
    await new StreamableHTTPClientTransport(new URL(MCP), { authProvider: provider }).finishAuth(await code);
    client = await connect();
  }
  return { client, saved };
}

const alan = mkdtempSync(join(tmpdir(), "gen9-mcp-alan-"));
const ada = mkdtempSync(join(tmpdir(), "gen9-mcp-ada-"));
const chats = [];
const theirs = [];
let adminRun = null;
let selfRegistered;
try {
  await revokeConsent();
  check(Boolean(await signInTerminal({ email: env.GEN9_SEED_USER_EMAIL, password: env.GEN9_SEED_USER_PASSWORD, configDir: alan })), "the seeded user signs in on the terminal");
  check(Boolean(await signInTerminal({ email: env.GEN9_SEED_ADMIN_EMAIL, password: env.GEN9_SEED_ADMIN_PASSWORD, configDir: ada })), "the seeded admin signs in on the terminal");

  // 1. The challenge and the metadata
  const bare = await fetch(MCP, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  const challenge = bare.headers.get("www-authenticate") ?? "";
  const metadata = await (await fetch(`${API}/.well-known/oauth-protected-resource/mcp`)).json();
  check(bare.status === 401 && challenge.includes('scope="gen9-mcp"') && challenge.includes("resource_metadata="), "without a token: 401, naming the metadata and the scope", challenge);
  check(metadata.resource === MCP && metadata.authorization_servers?.[0]?.endsWith("/realms/gen9"), "the metadata names this server and Gen9's Keycloak", JSON.stringify(metadata));

  // 2. OAuth, as an MCP client does it, with the pre-registered client
  const { client, saved } = await oauthClient("gen9-mcp", "Gen9 e2e");
  const consent = saved.consent;
  const auth = new URL(saved.authorizationUrl);
  check(auth.searchParams.get("code_challenge_method") === "S256" && auth.searchParams.get("resource") === MCP && /gen9-mcp/.test(auth.searchParams.get("scope") ?? ""), "the client found Keycloak from the metadata and asked with PKCE, the resource and the scope", `${auth.origin}${auth.pathname}`);
  check(/It will be able to:.*use Gen9 from this app/.test(consent.replace(/\s+/g, " ")), "the consent screen says what it allows", consent.replace(/\s+/g, " ").slice(0, 120));
  const token = saved.tokens?.access_token ?? "";
  const aud = [claims(token).aud].flat();
  check(aud.includes(MCP) && claims(token).azp === "gen9-mcp", "the token's audience is the MCP server", aud.join(", "));

  // 3. The tools
  const names = (await client.listTools()).tools.map((t) => t.name).sort();
  check(JSON.stringify(names) === JSON.stringify(["ask", "list_chats", "read_chat", "search_chats"]), "it lists its tools", names.join(", "));
  const first = (await client.callTool({ name: "ask", arguments: { message: `Reply with exactly this and nothing else: ${PHRASE}` } })).structuredContent;
  chats.push(first?.chat_id);
  check(first?.status === "done" && first.answer?.includes(PHRASE) && first.url?.endsWith(`/chat/${first.chat_id}`), "ask answers in a new chat, returning its id and address", JSON.stringify(first).slice(0, 160));
  const again = (await client.callTool({ name: "ask", arguments: { message: "What exactly did I ask you to reply with? Reply with that word only.", chat_id: first.chat_id } })).structuredContent;
  check(again?.chat_id === first.chat_id && again.answer?.includes(PHRASE), "ask with the chat's id continues it", again?.answer?.slice(0, 60));
  const read = (await client.callTool({ name: "read_chat", arguments: { chat_id: first.chat_id } })).structuredContent;
  check(read?.messages?.length === 4 && read.status === "done" && read.messages[0].content.includes(PHRASE), "read_chat returns its messages and state", `${read?.messages?.length} messages`);
  const listed = (await client.callTool({ name: "list_chats", arguments: { limit: 5 } })).structuredContent;
  check((listed?.result ?? listed)?.some?.((c) => c.chat_id === first.chat_id), "list_chats has it");
  // Both turns hold the phrase: once both are indexed, the chat must still be one hit (P2-I5)
  for (let i = 0; i < 30 && psql(`select count(*) from chat_search where thread_id = '${first.chat_id}'`) !== "2"; i++) {
    await new Promise((r) => setTimeout(r, 2000));
  }
  const hits = (await client.callTool({ name: "search_chats", arguments: { query: PHRASE, mode: "keyword" } })).structuredContent;
  const found = hits?.result ?? hits ?? [];
  check(
    found.filter((h) => h.chat_id === first.chat_id).length === 1,
    "search_chats finds it by its words, once for its two matching turns",
    `${found.length} hit(s)`,
  );

  // 4. Refusals
  const apiToken = await cliToken(alan);
  const withApiToken = await fetch(MCP, { method: "POST", headers: { Authorization: `Bearer ${apiToken}`, "Content-Type": "application/json", Accept: "application/json, text/event-stream" }, body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/list" }) });
  const onApi = await fetch(`${API}/v1/threads`, { headers: { Authorization: `Bearer ${token}` } });
  check(withApiToken.status === 401 && onApi.status === 401, "the API's token doesn't work on /mcp, and the MCP token doesn't work on the API", `${withApiToken.status} ${onApi.status}`);
  const adminChat = await (await fetch(`${API}/v1/threads`, { method: "POST", headers: { Authorization: `Bearer ${await cliToken(ada)}` } })).json();
  theirs.push(adminChat.id);
  // A run of another person's for step 6, stopped at once (a fresh install has no other person's runs)
  const adminAuth = { Authorization: `Bearer ${await cliToken(ada)}`, "Content-Type": "application/json" };
  adminRun = (await (await fetch(`${API}/v1/threads/${adminChat.id}/runs`, { method: "POST", headers: adminAuth, body: JSON.stringify({ message: "e2e: another person's run" }) })).json()).id;
  await fetch(`${API}/v1/threads/${adminChat.id}/runs/${adminRun}/cancel`, { method: "POST", headers: adminAuth });
  const other = await client.callTool({ name: "read_chat", arguments: { chat_id: adminChat.id } });
  check(other.isError && /No such chat/.test(other.content?.[0]?.text ?? ""), "another person's chat isn't there", other.content?.[0]?.text);
  // A message of spaces starts no run: it once did, and every attempt failed at its end (gen9-learn.md, M9, F8)
  const blank = await client.callTool({ name: "ask", arguments: { message: "   \n " } }).catch((e) => ({ isError: true, content: [{ text: String(e.message ?? e) }] }));
  check(blank.isError && /Write a message first/.test(blank.content?.[0]?.text ?? ""), "a message of spaces is refused, in words", blank.content?.[0]?.text?.slice(0, 120));

  // 5. The current protocol (2026-07-28, stateless), from FastMCP's Python client
  const python = execFileSync("uv", ["run", "-q", "python", "-c", `
import asyncio, json, os
from fastmcp.client import Client
from fastmcp.client.auth import BearerAuth
async def main():
    async with Client("${MCP}", auth=BearerAuth(os.environ["GEN9_MCP_TOKEN"])) as client:
        chats = (await client.call_tool("list_chats", {"limit": 5})).structured_content
        print(json.dumps({"version": client.protocol_version, "ids": [c["chat_id"] for c in chats.get("result", chats)]}))
asyncio.run(main())`], { cwd: `${ROOT}gen9-agent`, env: { ...process.env, GEN9_MCP_TOKEN: token }, encoding: "utf8" });
  const current = JSON.parse(python.trim().split("\n").at(-1));
  check(current.version === "2026-07-28" && current.ids.includes(first.chat_id), "FastMCP's Python client, on the current protocol (2026-07-28, server/discover), lists the same chat", current.version);

  // 6. MCP Tasks (SEP-2663, io.modelcontextprotocol/tasks): the official v2 client with the
  // extension's own requester (@modelcontextprotocol/ext-tasks) gets a task for `ask`, follows it,
  // answers what the run asks, and cancels one
  // Pinned to 2026-07-28 (server/discover): extensions are negotiated only on the modern era
  const v2 = new ClientV2({ name: "gen9-e2e-tasks", version: "1.0.0" }, { versionNegotiation: { mode: { pin: "2026-07-28" } } });
  await v2.connect(new HTTPV2(new URL(MCP), { requestInit: { headers: { Authorization: `Bearer ${token}` } } }));
  const asked = [];
  // The 2026-07-28 request path ext-tasks leaves to the host (its guide, "Supply the V2 request
  // path"): each framed request sent to /mcp with the era's headers, SEP-2243's Mcp-Name being a
  // tool's name or a task's id
  let rpcId = 100;
  const rawDispatch = async (request, options = {}) => {
    const name = request.params?.taskId ?? request.params?.name;
    const response = await fetch(MCP, {
      method: "POST",
      signal: options.signal,
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        Accept: "application/json, text/event-stream",
        "MCP-Protocol-Version": "2026-07-28",
        "Mcp-Method": request.method,
        ...(name ? { "Mcp-Name": name } : {}),
      },
      body: JSON.stringify({ jsonrpc: "2.0", id: rpcId++, ...request }),
    });
    const text = await response.text();
    const json = /event-stream/.test(response.headers.get("content-type") ?? "")
      ? JSON.parse(text.split("\n").filter((l) => l.startsWith("data:")).at(-1).slice(5))
      : JSON.parse(text);
    // DEBUG_TASKS=1 prints each reply (task states and results, never the token)
    if (process.env.DEBUG_TASKS) console.log("  <-", request.method, JSON.stringify(json).slice(0, 400));
    return json.error ? { kind: "error", error: json.error } : { kind: "result", result: json.result };
  };
  const session = createTaskSessionFromClient(v2, {
    endpointId: "gen9-e2e",
    rawDispatch,
    v2RequestFraming: { protocolVersion: "2026-07-28", clientInfo: { name: "gen9-e2e-tasks", version: "1.0.0" }, clientCapabilities: { elicitation: { form: {} } } },
    onInputRequest: createApplicationInputHandler({
      elicitation: async (request) => {
        asked.push(request.params);
        const fields = Object.keys(request.params?.requestedSchema?.properties ?? {});
        return { action: "accept", content: Object.fromEntries(fields.map((f) => [f, "Lisbon"])) };
      },
      sampling: async () => {
        throw new Error("no sampling here");
      },
      roots: async () => ({ roots: [] }),
    }),
  });
  try {
    const plain = await session.callTool("ask", { message: "Reply with exactly: tasks-ok" });
    const settled = await plain.settle();
    const answer = resultFromTaskOutcome(settled.outcome)?.structuredContent;
    chats.push(answer?.chat_id);
    check(
      plain.kind === "task" && settled.outcome.status === "completed" && answer?.status === "done" && answer.answer?.includes("tasks-ok"),
      "ask, from a client that declares MCP Tasks, becomes a task that completes with ask's own result",
      `${plain.kind}; ${settled.outcome.status}; era ${v2.getProtocolEra?.()}, server extensions ${JSON.stringify(v2.getServerCapabilities?.()?.extensions ?? null)}; ${JSON.stringify(answer).slice(0, 80)}`,
    );

    const question = await session.callTool("ask", { message: "Use your ask_user tool to ask me which city I'm travelling to, as one text question. After I answer, reply with exactly: going to <the city>." });
    const answered = await question.settle();
    const withCity = resultFromTaskOutcome(answered.outcome)?.structuredContent;
    chats.push(withCity?.chat_id);
    check(
      asked.length >= 1 && /city/i.test(JSON.stringify(asked[0])) && answered.outcome.status === "completed" && /Lisbon/.test(withCity?.answer ?? ""),
      "a run that asks the person becomes input_required; the answer, sent with tasks/update, reaches it",
      `asked ${asked.length}: ${JSON.stringify(asked[0] ?? {}).slice(0, 90)}; ${withCity?.answer?.slice(0, 60)}`,
    );

    const long = await session.callTool("ask", { message: "Write a 400-word story about a lighthouse keeper." });
    await long.cancel();
    const cancelled = await long.settle().catch((e) => ({ outcome: { status: `threw ${e.name}` } }));
    const runId = long.handle?.taskId;
    let runStatus = "";
    for (let i = 0; i < 30 && runStatus !== "cancelled"; i++) {
      runStatus = psql(`select status from runs where id = '${runId}'`);
      if (runStatus !== "cancelled") await new Promise((r) => setTimeout(r, 1000));
    }
    chats.push(psql(`select thread_id from runs where id = '${runId}'`));
    check(long.kind === "task" && runStatus === "cancelled", "tasks/cancel stops the run", `${cancelled.outcome.status}; run ${runStatus}`);

    // Another person's run isn't this person's task; nor is a made-up id
    const someoneElses = adminRun;
    const lookups = [];
    for (const taskId of [someoneElses, "00000000-0000-4000-8000-000000000000"].filter(Boolean)) {
      const view = session.task(taskId);
      const outcome = await view.result().then((o) => `answered ${o.status}`, (e) => String(e.message ?? e));
      lookups.push(outcome);
    }
    check(lookups.length === 2 && lookups.every((o) => /not found/i.test(o)), "another person's run, and a made-up id, are not found (-32602)", lookups.join(" | "));
  } finally {
    await session.close?.().catch(() => {});
  }
  // A client that didn't declare the extension can't use tasks/*: -32021
  const undeclared = await fetch(MCP, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", Accept: "application/json, text/event-stream", "MCP-Protocol-Version": "2026-07-28", "Mcp-Method": "tasks/get", "Mcp-Name": "00000000-0000-4000-8000-000000000000" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 9, method: "tasks/get", params: { taskId: "00000000-0000-4000-8000-000000000000", _meta: { "io.modelcontextprotocol/protocolVersion": "2026-07-28", "io.modelcontextprotocol/clientInfo": { name: "bare", version: "1" }, "io.modelcontextprotocol/clientCapabilities": {} } } }),
  });
  const undeclaredText = await undeclared.text();
  check(/-32021/.test(undeclaredText), "tasks/get from a client that didn't declare the extension gets -32021", undeclaredText.slice(0, 160));
  await client.close();

  // 7. A client that registers itself: its Client ID Metadata Document's URL is its client id,
  // served here at an address Keycloak's container reaches (host.docker.internal, allowed over
  // http in development: gen9-keycloak's GEN9_MCP_CLIENT_*)
  const discovery = await (await fetch(`${KEYCLOAK}/realms/gen9/.well-known/openid-configuration`)).json();
  check(discovery.client_id_metadata_document_supported === true, "Keycloak says it takes Client ID Metadata Documents");
  const docs = createServer();
  await new Promise((resolve) => docs.listen(0, "0.0.0.0", resolve));
  servers.push(docs);
  const documentUrl = `http://host.docker.internal:${docs.address().port}/gen9-e2e-client.json`;
  let fetched = 0;
  let documentFor;
  docs.on("request", (req, res) => {
    fetched++;
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify({ client_id: documentUrl, client_name: "Gen9 e2e (self-registered)", redirect_uris: [documentFor], grant_types: ["authorization_code", "refresh_token"], response_types: ["code"], token_endpoint_auth_method: "none" }));
  });
  selfRegistered = documentUrl;
  const own = await oauthClient((redirect) => ((documentFor = redirect), documentUrl), "Gen9 e2e (self-registered)");
  const ownToken = own.saved.tokens?.access_token ?? "";
  check(fetched >= 1 && /Gen9 e2e \(self-registered\)/.test(own.saved.consent) && claims(ownToken).azp === documentUrl && [claims(ownToken).aud].flat().includes(MCP), "a client identified by its metadata document: Keycloak fetched it, asked for consent naming it, and issued a token for this server", `fetched ${fetched}; azp ${claims(ownToken).azp}`);
  const ownChats = (await own.client.callTool({ name: "list_chats", arguments: { limit: 5 } })).structuredContent;
  check((ownChats?.result ?? ownChats)?.some?.((c) => c.chat_id === first.chat_id), "its token works on the tools, as the same person");
  await own.client.close();

  // 8. The person sees both in Settings, Apps with access, and takes back their access there: each
  // is signed out, its refresh token refused (gen9-learn.md, M9, F12). The self-registered one's
  // client id is its document's URL, slashes and all
  const APP = process.env.APP_URL ?? "http://localhost:14000";
  await page.goto(`${APP}/auth/login?returnTo=/settings`, { waitUntil: "networkidle0" });
  const apps = () => page.evaluate(() => [...document.querySelectorAll("section")].find((s) => s.querySelector("h2")?.textContent === "Apps with access")?.innerText ?? "");
  const appsListed = await apps();
  check(
    appsListed.includes("Agents (MCP and A2A)") && appsListed.includes("Gen9 e2e (self-registered)") && appsListed.includes("use Gen9 from this app: ask it, and read and search your chats"),
    "Settings lists both apps the person let in, with what each may do in the consent screen's words",
    appsListed.replace(/\s+/g, " ").slice(0, 220),
  );
  for (const name of ["Agents (MCP and A2A)", "Gen9 e2e (self-registered)"]) {
    await page.click(`button[aria-label="Remove access for ${name}"]`);
    await page.waitForSelector("[role=alertdialog]");
    for (const button of await page.$$("[role=alertdialog] button")) if ((await button.evaluate((b) => b.textContent.trim())) === "Remove access") await button.click();
    await page.waitForFunction((n) => !document.querySelector(`button[aria-label="Remove access for ${n}"]`), { timeout: 15_000 }, name).catch(() => {});
  }
  const refresh = async (clientId, tokens) =>
    (await fetch(`${KEYCLOAK}/realms/gen9/protocol/openid-connect/token`, { method: "POST", body: new URLSearchParams({ grant_type: "refresh_token", client_id: clientId, refresh_token: tokens?.refresh_token ?? "" }) })).status;
  const left = await apps();
  const refused = [await refresh("gen9-mcp", saved.tokens), await refresh(documentUrl, own.saved.tokens)];
  check(
    !left.includes("Agents (MCP and A2A)") && !left.includes("Gen9 e2e (self-registered)") && refused.every((status) => status === 400),
    "Remove access takes each back: gone from the list, and its refresh token refused",
    `refresh: ${refused.join(", ")}`,
  );
} catch (e) {
  check(false, "the run finished", e.stack ?? String(e));
} finally {
  const apiToken = await cliToken(alan).catch(() => null);
  for (const id of chats.filter(Boolean)) await fetch(`${API}/v1/threads/${id}`, { method: "DELETE", headers: { Authorization: `Bearer ${apiToken}` } }).catch(() => {});
  const adaToken = await cliToken(ada).catch(() => null);
  for (const id of theirs) await fetch(`${API}/v1/threads/${id}`, { method: "DELETE", headers: { Authorization: `Bearer ${adaToken}` } }).catch(() => {});
  await revokeConsent().catch(() => {});
  // Keycloak keeps a self-registered client (its issue #45284): removed, with its consent
  if (selfRegistered) {
    const [kept] = (await admin(`/clients?clientId=${encodeURIComponent(selfRegistered)}`)) ?? [];
    if (kept) await admin(`/clients/${kept.id}`, { method: "DELETE" });
  }
  await browser.close();
  for (const server of servers) server.close();
  for (const dir of [alan, ada]) rmSync(dir, { recursive: true, force: true });
}
console.log(failures ? `\n${failures} check(s) failed` : "\nall MCP server checks passed");
process.exit(failures ? 1 : 0);
