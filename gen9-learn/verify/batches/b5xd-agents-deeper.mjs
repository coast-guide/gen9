// Batch 8, deeper: what other programs reach, after b5x, with the clients it signed in (ctx.agents).
//   tools       the MCP server's other tools as the person: list_chats, search_chats, read_chat,
//               each result structured, with the chat's id; the MCP token opens nothing else
//   reach       an A2A agent sees only the chats it started: its task's chat is one of the person's,
//               but their own chats aren't among its tasks, and the web app's first chat isn't a task
//   self        a client that registers itself: its Client ID Metadata Document's URL is its client
//               id; Keycloak fetches the document, names the client on the consent screen, and issues
//               a token for the MCP server (served here at host.docker.internal, which Keycloak reaches)
// No model call. Tokens are used in memory and dropped, and the self-registered client is deleted.
import { createServer } from "node:http";
import { Client as McpClient, StreamableHTTPClientTransport } from "@modelcontextprotocol/client";
import { API, KEYCLOAK, admin, appdb, check } from "../lib.mjs";

const MCP = `${API}/mcp`;
const claims = (jwt) => JSON.parse(Buffer.from((jwt ?? "..").split(".")[1] ?? "", "base64url").toString() || "{}");
const structured = (result) => result?.structuredContent?.result ?? result?.structuredContent;

export default async function agentsDeeper(ctx) {
  const { user } = ctx;
  const obs = {};
  const { oauth, mcp, a2a } = ctx.agents;

  // ---- tools: list, search, read, as the person
  const listed = structured(await mcp.client.callTool({ name: "list_chats", arguments: { limit: 20 } }));
  obs.listChats = { count: listed?.length, first: listed?.[0] ? Object.keys(listed[0]).join(", ") : "" };
  const hits = structured(await mcp.client.callTool({ name: "search_chats", arguments: { query: "lighthouses", mode: "keyword", limit: 20 } }));
  // By then several chats mention lighthouses (part 2, the background task, b5x's "lighthouse-…" replies): the first
  // chat is among them. BM25 favours short texts, so b5x's one-line replies rank above it and the default 5 missed it
  // (in one full run)
  obs.searchChats = { count: hits?.length, first: hits?.[0] ? Object.keys(hits[0]).join(", ") : "", hasFirstChat: !!hits?.some((h) => h.chat_id === ctx.threadId) };
  const read = structured(await mcp.client.callTool({ name: "read_chat", arguments: { chat_id: ctx.threadId } }));
  obs.readChat = { keys: read ? Object.keys(read).join(", ") : "", messages: read?.messages?.length, status: read?.status };
  check(
    obs.listChats.count > 3 && obs.searchChats.hasFirstChat && obs.readChat.messages >= 4,
    "list_chats, search_chats and read_chat answer as the person: their chats, the first chat among the hits, its messages",
    `list: ${obs.listChats.count} chats {${obs.listChats.first}}; search: ${obs.searchChats.count} hits {${obs.searchChats.first}}; read: {${obs.readChat.keys}}, ${obs.readChat.messages} messages`,
  );
  // The MCP token is for /mcp only: the API refuses it
  const onApi = await fetch(`${API}/v1/threads`, { headers: { Authorization: `Bearer ${mcp.token}` } });
  obs.mcpTokenOnApi = `${onApi.status} ${(onApi.headers.get("www-authenticate") ?? "").slice(0, 120)}`;
  check(onApi.status === 401, "the MCP token opens nothing else: the API answers 401", obs.mcpTokenOnApi);

  // ---- reach: an A2A agent's tasks are the chats it started, and nothing more
  const all = await a2a.agent.listTasks({ tenant: "", contextId: "", status: 0, pageSize: 100, pageToken: "", historyLength: undefined, statusTimestampAfter: undefined, includeArtifacts: undefined });
  obs.a2aTasks = (all.tasks ?? []).map((t) => t.contextId);
  obs.a2aOwnChats = appdb(`select count(*) from threads t join users u on u.id = t.user_id where u.sub = '${user.sub}' and t.a2a_client is not null`);
  obs.personChats = appdb(`select count(*) from threads t join users u on u.id = t.user_id where u.sub = '${user.sub}' and t.a2a_client is null and t.parent_id is null`);
  const webRun = appdb(`select id from runs where thread_id = '${ctx.threadId}' order by created_at limit 1`);
  obs.webRunAsTask = await a2a.agent.getTask({ tenant: "", id: webRun, historyLength: undefined }).then(() => "found", (e) => String(e.message ?? e).slice(0, 80));
  obs.a2aClient = appdb(`select a2a_client from threads where id = '${a2a.context}'`);
  check(
    obs.a2aTasks.length >= 1 && obs.a2aTasks.every((c) => c === a2a.context) && obs.webRunAsTask !== "found",
    "an A2A agent lists only the tasks of the chats it started; the person's own chats, and their runs, aren't its tasks",
    `${obs.a2aTasks.length} task(s), all in its chat (a2a_client ${obs.a2aClient}); the person has ${obs.personChats} chats of their own; a web chat's run as a task: ${obs.webRunAsTask}`,
  );

  // ---- self: a client that registers itself by its metadata document's URL
  const discovery = await (await fetch(`${KEYCLOAK}/realms/gen9/.well-known/openid-configuration`)).json();
  obs.cimdSupported = discovery.client_id_metadata_document_supported;
  const docs = createServer();
  await new Promise((resolve) => docs.listen(0, "0.0.0.0", resolve));
  const documentUrl = `http://host.docker.internal:${docs.address().port}/gen9-learn-client.json`;
  let fetched = 0;
  let redirectFor = "";
  docs.on("request", (req, res) => {
    fetched++;
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify({ client_id: documentUrl, client_name: "gen9-learn's own client", redirect_uris: [redirectFor], grant_types: ["authorization_code", "refresh_token"], response_types: ["code"], token_endpoint_auth_method: "none" }));
  });
  try {
    const own = await oauth("openid gen9-mcp", (redirect) => ((redirectFor = redirect), documentUrl));
    const ownClaims = claims(own.token);
    obs.cimd = { fetched, consentNames: /gen9-learn's own client/.test(own.consent), azp: ownClaims.azp === documentUrl ? "the document's URL" : ownClaims.azp, aud: [ownClaims.aud].flat() };
    const client = new McpClient({ name: "gen9-learn-own", version: "1.0.0" }, { versionNegotiation: { mode: { pin: "2026-07-28" } } });
    await client.connect(new StreamableHTTPClientTransport(new URL(MCP), { requestInit: { headers: { Authorization: `Bearer ${own.token}` } } }));
    const theirs = structured(await client.callTool({ name: "list_chats", arguments: { limit: 20 } }));
    obs.cimdSamePerson = theirs?.length === listed?.length;
    await client.close();
    check(
      obs.cimdSupported === true && fetched >= 1 && obs.cimd.consentNames && obs.cimd.azp === "the document's URL" && obs.cimd.aud.includes(MCP) && obs.cimdSamePerson,
      "a client registers itself by its metadata document: Keycloak fetches it, names it on the consent screen, and issues a token for the MCP server, as the same person",
      JSON.stringify(obs.cimd),
    );
  } finally {
    docs.close();
    // Keycloak keeps a client it registered by its document: removed, so runs don't pile them up
    const [kept] = (await admin(`/clients?clientId=${encodeURIComponent(documentUrl)}`).catch(() => null)) ?? [];
    if (kept) await admin(`/clients/${kept.id}`, { method: "DELETE" }).catch(() => {});
  }
  await mcp.client.close().catch(() => {});
  delete ctx.agents;
  return obs;
}
