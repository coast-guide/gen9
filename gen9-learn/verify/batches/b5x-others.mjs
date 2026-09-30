// Batch 5x, other agents call it: Gen9 as an MCP server (its metadata, 401 without a token, an
// MCP client's OAuth sign-in with PKCE and consent, its tools, and `ask` as an MCP task), over AG-UI
// (a run posted with the CLI's token, its events streamed back) and over A2A (the Agent Card, the
// sign-in it declares, a task completed). Tokens are used and dropped, never printed.
import { spawn } from "node:child_process";
import { createHash, randomBytes, randomUUID } from "node:crypto";
import { createServer } from "node:http";
import { mkdirSync, readFileSync, rmSync } from "node:fs";
import { ClientFactory, JsonRpcTransportFactory } from "@a2a-js/sdk/client";
import { Client as McpClient, StreamableHTTPClientTransport } from "@modelcontextprotocol/client";
import { createApplicationInputHandler, createTaskSessionFromClient, resultFromTaskOutcome } from "@modelcontextprotocol/ext-tasks/client";
import { API, KEYCLOAK, ROOT, appdb, check, navigation, nextWindow, otpPolicy, settled, totp } from "../lib.mjs";

const MCP = `${API}/mcp`;
// A2A 1.0's enums as the JS SDK has them
const ROLE_USER = 1;
const COMPLETED = 3;
const claims = (jwt) => JSON.parse(Buffer.from((jwt ?? "..").split(".")[1] ?? "", "base64url").toString() || "{}");

export default async function others(ctx) {
  const { page, rec, user } = ctx;
  const obs = {};
  const policy = await otpPolicy();
  // Keycloak's pages on the way: sign in (password, then the authenticator code) if asked, then
  // consent, whose text is kept
  const through = async () => {
    let consent = "";
    for (let step = 0; step < 6; step++) {
      await settled(page);
      if (await page.$("#password")) {
        if (await page.$("#username")) await page.type("#username", user.email);
        await page.type("#password", user.password);
        await navigation(page, page.click("#kc-login"));
      } else if (await page.$("#otp")) {
        user.otpCounter = await nextWindow(policy, user.otpCounter);
        await page.type("#otp", totp(user.totpSecret, policy, user.otpCounter));
        await navigation(page, page.click("#kc-login"));
      } else if (await page.$('button[name="accept"]')) {
        consent = await page.$eval("main, body", (m) => m.textContent.replace(/\s+/g, " "));
        await navigation(page, page.click('button[name="accept"]')).catch(() => {});
      } else break;
    }
    return consent;
  };
  // An MCP client's sign-in, as the spec has it: authorization code with PKCE, a loopback redirect,
  // the pre-registered public client gen9-mcp (or, for b5xd, another client id: `clientFor` gets the
  // redirect and returns it), and the scope that sets the token's audience
  const oauth = async (scope, clientFor = () => "gen9-mcp") => {
    const callback = createServer();
    await new Promise((resolve) => callback.listen(0, "127.0.0.1", resolve));
    const redirect = `http://127.0.0.1:${callback.address().port}/callback`;
    const clientId = clientFor(redirect);
    const code = new Promise((resolve) => callback.on("request", (req, res) => (resolve(new URL(req.url, redirect).searchParams.get("code")), res.end("Signed in. You can close this tab."))));
    const verifier = randomBytes(32).toString("base64url");
    const auth = new URL(`${KEYCLOAK}/realms/gen9/protocol/openid-connect/auth`);
    for (const [k, v] of Object.entries({ client_id: clientId, response_type: "code", redirect_uri: redirect, scope, code_challenge: createHash("sha256").update(verifier).digest("base64url"), code_challenge_method: "S256", state: randomUUID() })) auth.searchParams.set(k, v);
    await page.goto(auth.toString(), { waitUntil: "networkidle0" });
    const consent = await through();
    const tokens = await (await fetch(`${KEYCLOAK}/realms/gen9/protocol/openid-connect/token`, { method: "POST", body: new URLSearchParams({ grant_type: "authorization_code", code: await code, redirect_uri: redirect, client_id: clientId, code_verifier: verifier }) })).json();
    callback.close();
    return { token: tokens.access_token ?? "", consent };
  };

  // 5x.1 Gen9 as an MCP server: the metadata, and no token, no entry
  rec.mark("5x.1 mcp server");
  const metadata = await (await fetch(`${API}/.well-known/oauth-protected-resource/mcp`)).json();
  const bare = await fetch(MCP, { method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json, text/event-stream" }, body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/list" }) });
  obs.mcpMetadata = { resource: metadata.resource, authorization_servers: metadata.authorization_servers, scopes: metadata.scopes_supported };
  obs.mcpBare = `${bare.status} ${(bare.headers.get("www-authenticate") ?? "").replace(/\s+/g, " ").slice(0, 160)}`;
  check(
    metadata.resource === MCP && metadata.authorization_servers?.[0]?.endsWith("/realms/gen9") && bare.status === 401 && /resource_metadata=/.test(obs.mcpBare),
    "the MCP server's metadata names it and Gen9's Keycloak; without a token, 401 pointing to the metadata",
    `${metadata.resource}; ${metadata.authorization_servers?.[0]}; ${obs.mcpBare.slice(0, 90)}`,
  );

  // 5x.2 An MCP client signs in and asks, as a task (MCP Tasks)
  rec.mark("5x.2 mcp client");
  const mcp = await oauth("openid gen9-mcp");
  const mcpClaims = claims(mcp.token);
  obs.mcpConsent = mcp.consent.match(/use Gen9 from this app[^.]*/)?.[0] ?? mcp.consent.slice(0, 120);
  obs.mcpToken = { azp: mcpClaims.azp, aud: mcpClaims.aud, scope: mcpClaims.scope };
  const client = new McpClient({ name: "gen9-learn", version: "1.0.0" }, { versionNegotiation: { mode: { pin: "2026-07-28" } } });
  await client.connect(new StreamableHTTPClientTransport(new URL(MCP), { requestInit: { headers: { Authorization: `Bearer ${mcp.token}` } } }));
  obs.mcpTools = (await client.listTools()).tools.map((t) => t.name).sort();
  obs.mcpExtensions = Object.keys(client.getServerCapabilities?.()?.extensions ?? {});
  // ext-tasks leaves the 2026-07-28 request path to the host: each request framed and posted as is
  let id = 1;
  const rawDispatch = async (request, options = {}) => {
    const name = request.params?.taskId ?? request.params?.name;
    const response = await fetch(MCP, {
      method: "POST",
      signal: options.signal,
      headers: { Authorization: `Bearer ${mcp.token}`, "Content-Type": "application/json", Accept: "application/json, text/event-stream", "MCP-Protocol-Version": "2026-07-28", "Mcp-Method": request.method, ...(name ? { "Mcp-Name": name } : {}) },
      body: JSON.stringify({ jsonrpc: "2.0", id: id++, ...request }),
    });
    const text = await response.text();
    const json = /event-stream/.test(response.headers.get("content-type") ?? "") ? JSON.parse(text.split("\n").filter((l) => l.startsWith("data:")).at(-1).slice(5)) : JSON.parse(text);
    return json.error ? { kind: "error", error: json.error } : { kind: "result", result: json.result };
  };
  const session = createTaskSessionFromClient(client, {
    endpointId: "gen9-learn",
    rawDispatch,
    v2RequestFraming: { protocolVersion: "2026-07-28", clientInfo: { name: "gen9-learn", version: "1.0.0" }, clientCapabilities: { elicitation: { form: {} } } },
    onInputRequest: createApplicationInputHandler({ elicitation: async () => ({ action: "decline" }), sampling: async () => { throw new Error("no sampling"); }, roots: async () => ({ roots: [] }) }),
  });
  try {
    const asked = await session.callTool("ask", { message: "Reply with exactly: lighthouse-mcp" });
    const { outcome } = await asked.settle();
    const answer = resultFromTaskOutcome(outcome)?.structuredContent ?? {};
    obs.mcpTask = { kind: asked.kind, taskIdIsRun: appdb(`select count(*) from runs where id = '${asked.handle?.taskId}'`) === "1", outcome: outcome.status, status: answer.status, answer: answer.answer };
    obs.mcpChatIsTheirs = appdb(`select count(*) from threads t join users u on u.id = t.user_id where t.id = '${answer.chat_id}' and u.sub = '${user.sub}'`) === "1";
  } finally {
    await session.close?.().catch(() => {});
  }
  check(
    /It will be able to:.*use Gen9 from this app/.test(mcp.consent) && [mcpClaims.aud].flat().includes(MCP) && obs.mcpTools.join() === "ask,list_chats,read_chat,search_chats" && obs.mcpExtensions.includes("io.modelcontextprotocol/tasks"),
    "an MCP client signs in (PKCE, consent \"use Gen9 from this app\", a token for the MCP server) and lists Gen9's four tools; the server offers MCP Tasks",
    `${obs.mcpConsent.slice(0, 60)}; aud ${[mcpClaims.aud].flat().join(",")}; ${obs.mcpTools.join(", ")}; ${obs.mcpExtensions.join(", ")}`,
  );
  check(
    obs.mcpTask.kind === "task" && obs.mcpTask.taskIdIsRun && obs.mcpTask.outcome === "completed" && obs.mcpTask.status === "done" && /lighthouse-mcp/.test(obs.mcpTask.answer ?? "") && obs.mcpChatIsTheirs,
    "ask, from a client that declares MCP Tasks: a task whose id is the run's, completed with ask's result, in a chat of the person's",
    JSON.stringify(obs.mcpTask).slice(0, 160),
  );

  // 5x.3 AG-UI: a run posted with the CLI's token, its events read back as they stream
  rec.mark("5x.3 ag-ui");
  const config = new URL("../out/cli-config-5x", import.meta.url).pathname;
  rmSync(config, { recursive: true, force: true });
  mkdirSync(config, { recursive: true });
  const env = { ...process.env, GEN9_CONFIG_DIR: config };
  const login = () =>
    new Promise((resolve) => {
      const child = spawn("uv", ["run", "-q", "gen9", "login"], { cwd: `${ROOT}gen9-cli`, env });
      let out = "";
      let confirming = false;
      const onData = (data) => {
        out += data;
        const url = out.match(/http\S+user_code=\S+/)?.[0];
        if (url && !confirming) {
          confirming = true;
          page.goto(url, { waitUntil: "networkidle0" }).then(through).catch((e) => (out += `\n(confirm failed: ${e.message})`));
        }
      };
      child.stdout.on("data", onData);
      child.stderr.on("data", onData);
      child.on("exit", (code) => resolve(code));
    });
  const loggedIn = await login();
  const apiToken = JSON.parse(readFileSync(`${config}/credentials.json`, "utf8")).access_token;
  const thread = randomUUID();
  const streamed = await fetch(`${API}/v1/agui`, {
    method: "POST",
    headers: { Authorization: `Bearer ${apiToken}`, "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ threadId: thread, runId: randomUUID(), messages: [{ id: randomUUID(), role: "user", content: "Reply with exactly: lighthouse-agui" }], tools: [], context: [], state: {}, forwardedProps: {} }),
  });
  const events = (await streamed.text()).split("\n").filter((l) => l.startsWith("data:")).map((l) => JSON.parse(l.slice(5)));
  obs.aguiTypes = [...new Set(events.map((e) => e.type))];
  obs.aguiText = events.filter((e) => e.type === "TEXT_MESSAGE_CONTENT").map((e) => e.delta).join("");
  obs.aguiChat = appdb(`select count(*) from threads t join users u on u.id = t.user_id where t.id = '${thread}' and u.sub = '${user.sub}'`);
  check(
    loggedIn === 0 && streamed.status === 200 && obs.aguiTypes[0] === "RUN_STARTED" && obs.aguiTypes.at(-1) === "RUN_FINISHED" && obs.aguiTypes.includes("TEXT_MESSAGE_CONTENT") && /lighthouse-agui/.test(obs.aguiText) && obs.aguiChat === "1",
    "AG-UI: a run posted with the CLI's token streams RUN_STARTED, the answer's text, RUN_FINISHED; its thread is a chat of the person's",
    `${obs.aguiTypes.join(", ")}; "${obs.aguiText.slice(0, 40)}"`,
  );
  await new Promise((resolve) => spawn("uv", ["run", "-q", "gen9", "logout"], { cwd: `${ROOT}gen9-cli`, env }).on("exit", resolve));
  rmSync(config, { recursive: true, force: true });

  // 5x.4 A2A: the Agent Card, the sign-in it declares, a task
  rec.mark("5x.4 a2a");
  const card = await (await fetch(`${API}/.well-known/agent-card.json`)).json();
  const flow = card.securitySchemes?.keycloak?.oauth2SecurityScheme?.flows?.authorizationCode;
  obs.a2aCard = { url: card.supportedInterfaces?.[0]?.url, binding: card.supportedInterfaces?.[0]?.protocolBinding, streaming: card.capabilities?.streaming, pkce: flow?.pkceRequired, scopes: Object.keys(flow?.scopes ?? {}) };
  const a2aBare = await fetch(`${API}/a2a`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  const a2a = await oauth("openid gen9-a2a");
  obs.a2aConsent = a2a.consent.match(/work with Gen9 for you[^.]*/)?.[0] ?? a2a.consent.slice(0, 120);
  obs.a2aAud = [claims(a2a.token).aud].flat();
  const signed = (input, init = {}) => fetch(input, { ...init, headers: { ...Object.fromEntries(new Headers(init.headers ?? {})), Authorization: `Bearer ${a2a.token}` } });
  const agent = await new ClientFactory({ transports: [new JsonRpcTransportFactory({ fetchImpl: signed })] }).createFromUrl(API);
  const task = await agent.sendMessage({ tenant: "", message: { messageId: randomUUID(), role: ROLE_USER, parts: [{ content: { $case: "text", value: "Reply with exactly: lighthouse-a2a" } }], contextId: "", taskId: "", metadata: undefined, extensions: [], referenceTaskIds: [] }, configuration: undefined, metadata: undefined });
  obs.a2aTask = {
    state: task.status?.state,
    answer: (task.artifacts ?? []).flatMap((a) => a.parts).map((p) => (p.content?.$case === "text" ? p.content.value : "")).join(""),
    contextIsChat: appdb(`select count(*) from threads t join users u on u.id = t.user_id where t.id = '${task.contextId}' and u.sub = '${user.sub}'`) === "1",
  };
  check(
    obs.a2aCard.url === `${API}/a2a` && obs.a2aCard.pkce === true && obs.a2aCard.scopes.includes("gen9-a2a") && a2aBare.status === 401,
    "A2A: the Agent Card names the endpoint, streaming and Keycloak's sign-in (PKCE, scope gen9-a2a); without a token, 401",
    `${JSON.stringify(obs.a2aCard)}; ${a2aBare.status}`,
  );
  check(
    /It will be able to:.*work with Gen9 for you/.test(a2a.consent) && obs.a2aAud.includes(`${API}/a2a`) && obs.a2aTask.state === COMPLETED && /lighthouse-a2a/.test(obs.a2aTask.answer) && obs.a2aTask.contextIsChat,
    "an agent signs in by the card's flow (consent \"work with Gen9 for you\", a token for /a2a), and its task completes; its context is a chat of the person's",
    `${obs.a2aConsent.slice(0, 50)}; aud ${obs.a2aAud.join(",")}; ${obs.a2aTask.state}; "${obs.a2aTask.answer.slice(0, 30)}"`,
  );
  // For b5xd: the signed-in clients, kept in memory only (tokens are never written anywhere)
  ctx.agents = { oauth, mcp: { client, token: mcp.token, chat: null }, a2a: { agent, token: a2a.token, context: task.contextId, task: task.id } };
  return obs;
}
