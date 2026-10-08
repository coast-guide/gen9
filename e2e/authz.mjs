// Authorization on every route of gen9-agent's API (docs/plans/harness.md, "Auth across the
// harness"; OWASP API Security Top 10: API1 object level, API2 authentication, API5 function
// level). The routes come from the API's own OpenAPI document, so a new route is checked without
// being listed here. Three rules:
//   1. no token: every route answers 401 (a task's /fire takes its own token, so 401 too)
//   2. a person who isn't an admin: every /v1/admin route answers 403
//   3. another person, with the seeded user's real ids (a chat with a run and a file, a task with
//      a trigger, an environment secret, a connector): every route answers 403 or 404, never 2xx
//      or 5xx, and afterwards the seeded user's things are all still there
// Bodies and required query parameters are built from the OpenAPI schemas, so a 422 can't hide
// whether the check held; a 422 is reported as inconclusive. The seeded user's own GETs must
// answer 200, which shows the ids are real. Plugin routes are left to plugins.mjs: a plugin given
// to everyone is rightly everyone's to add. It deletes what it made (a throwaway second person,
// the seeded user's chat, task, secret and connector), and costs one short reply.
import { randomBytes, randomUUID } from "node:crypto";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawn } from "node:child_process";
import { deleteChats } from "./chats.mjs";
import { ROOT, signInTerminal } from "./signin.mjs";
import { forget } from "./forget.mjs";

const env = Object.fromEntries(
  readFileSync(`${ROOT}gen9-keycloak/.env`, "utf8")
    .split("\n")
    .filter((line) => /^[A-Z0-9_]+=/.test(line))
    .map((line) => [line.slice(0, line.indexOf("=")), line.slice(line.indexOf("=") + 1)]),
);
const API = process.env.GEN9_API ?? "http://localhost:17000";
const KEYCLOAK = process.env.KEYCLOAK_URL ?? "http://localhost:15000";
const TAG = randomBytes(3).toString("hex");
const EMAIL = `authz-${Date.now()}@gen9.test`;
const PASSWORD = `e2e-${randomBytes(12).toString("hex")}`;

let failures = 0;
let inconclusive = 0;
function check(ok, what, detail = "") {
  console.log(`${ok ? "ok  " : "FAIL"}  ${what}${detail ? `: ${detail}` : ""}`);
  if (!ok) failures++;
  return ok;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function admin(path, init = {}) {
  const token = await fetch(`${KEYCLOAK}/realms/master/protocol/openid-connect/token`, {
    method: "POST",
    body: new URLSearchParams({ grant_type: "password", client_id: "admin-cli", username: env.KC_BOOTSTRAP_ADMIN_USERNAME, password: env.KC_BOOTSTRAP_ADMIN_PASSWORD }),
  })
    .then((r) => r.json())
    .then((body) => body.access_token);
  const response = await fetch(`${KEYCLOAK}/admin/realms/gen9${path}`, { ...init, headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" } });
  if (!response.ok && response.status !== 404) throw new Error(`Keycloak ${init.method ?? "GET"} ${path}: ${response.status}`);
  return [201, 204, 404].includes(response.status) ? null : response.json();
}

const whoami = (configDir) =>
  new Promise((resolve) => {
    const child = spawn("uv", ["run", "-q", "gen9", "whoami"], { cwd: `${ROOT}gen9-cli`, env: { ...process.env, GEN9_CONFIG_DIR: configDir }, stdio: "ignore" });
    child.on("exit", resolve);
  });
async function token(configDir) {
  await whoami(configDir);
  return JSON.parse(readFileSync(join(configDir, "credentials.json"), "utf8")).access_token;
}
// One request; streams are cut after their headers (a refused one ends at once anyway)
async function call(bearer, method, path, { body, raw } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 20_000);
  try {
    const response = await fetch(`${API}${path}`, {
      method,
      signal: controller.signal,
      headers: { ...(bearer ? { Authorization: `Bearer ${bearer}` } : {}), ...(raw ? {} : { "Content-Type": "application/json" }) },
      body: raw ?? (body === undefined ? undefined : JSON.stringify(body)),
    });
    const type = response.headers.get("content-type") ?? "";
    const text = type.includes("event-stream") ? "(stream)" : await response.text();
    controller.abort();
    let json = null;
    try {
      json = text && text !== "(stream)" ? JSON.parse(text) : null;
    } catch {}
    return { status: response.status, text, json };
  } catch (e) {
    return { status: 0, text: String(e.message ?? e) };
  } finally {
    clearTimeout(timer);
  }
}

// A minimal valid value for an OpenAPI schema: required fields only, enums' first value
function sample(schema, spec, depth = 0) {
  if (!schema || depth > 6) return null;
  if (schema.$ref) return sample(spec.components.schemas[schema.$ref.split("/").pop()], spec, depth + 1);
  const options = schema.anyOf ?? schema.oneOf;
  if (options) return sample(options.find((o) => o.type !== "null") ?? options[0], spec, depth + 1);
  if (schema.allOf) return sample(schema.allOf[0], spec, depth + 1);
  if (schema.enum) return schema.enum[0];
  if (schema.const !== undefined) return schema.const;
  switch (schema.type) {
    case "object": {
      const out = {};
      for (const key of schema.required ?? []) out[key] = sample(schema.properties?.[key], spec, depth + 1);
      return out;
    }
    case "array":
      return schema.minItems ? [sample(schema.items, spec, depth + 1)] : [];
    case "integer":
    case "number":
      return schema.minimum ?? 1;
    case "boolean":
      return false;
    default:
      if (schema.format === "uuid") return randomUUID();
      if (schema.pattern === "^\\d{2}:\\d{2}$") return "03:00";
      return "x".repeat(Math.max(1, schema.minLength ?? 1));
  }
}

const seeded = mkdtempSync(join(tmpdir(), "gen9-authz-alan-"));
const other = mkdtempSync(join(tmpdir(), "gen9-authz-other-"));
let otherId;
const mine = {};
try {
  check(Boolean(await signInTerminal({ email: env.GEN9_SEED_USER_EMAIL, password: env.GEN9_SEED_USER_PASSWORD, configDir: seeded })), "the seeded user signs in on the terminal");
  await admin("/users", {
    method: "POST",
    body: JSON.stringify({ username: EMAIL, email: EMAIL, firstName: "Authz", lastName: "Check", enabled: true, emailVerified: true, credentials: [{ type: "password", value: PASSWORD, temporary: false }] }),
  });
  otherId = (await admin(`/users?email=${encodeURIComponent(EMAIL)}&exact=true`))[0].id;
  check(Boolean(await signInTerminal({ email: EMAIL, password: PASSWORD, configDir: other })), "a second person (not an admin) signs in on the terminal");

  // The seeded user's things, whose ids the second person tries
  let a = await token(seeded);
  mine.thread = (await call(a, "POST", "/v1/threads")).json.id;
  mine.run = (await call(a, "POST", `/v1/threads/${mine.thread}/runs`, { body: { message: "Reply with only: ok", permission_mode: "auto" } })).json.id;
  for (let i = 0; i < 120; i++) {
    const run = await call(a, "GET", `/v1/threads/${mine.thread}/runs/${mine.run}`);
    if (["success", "error", "cancelled", "expired"].includes(run.json?.status)) break;
    await sleep(1000);
  }
  mine.file = (await call(a, "POST", `/v1/threads/${mine.thread}/files?name=authz-${TAG}.txt`, { raw: "authz" })).json?.id;
  mine.task = (await call(a, "POST", "/v1/tasks", { body: { name: `e2e authz ${TAG}`, prompt: "Reply with only: ok", schedule: { kind: "daily", time: "03:00" }, time_zone: "UTC" } })).json.id;
  mine.trigger = (await call(a, "POST", `/v1/tasks/${mine.task}/trigger`)).json?.token;
  mine.secret = (await call(a, "POST", "/v1/me/environment-secrets", { body: { name: `authz-${TAG}`, host: "httpbin.org", value: `v-${TAG}` } })).json?.id;
  mine.connector = (await call(a, "POST", "/v1/me/connectors", { body: { name: `authz-${TAG}`, url: "https://mcp.deepwiki.com/mcp" } })).json?.id;
  check(Object.values(mine).every(Boolean), "the seeded user has a chat, a run, a file, a task, a trigger, a secret and a connector", Object.entries(mine).filter(([, v]) => !v).map(([k]) => `no ${k}`).join(", "));

  const spec = await (await fetch(`${API}/openapi.json`)).json();
  const ids = { thread_id: mine.thread, run_id: mine.run, file_id: mine.file, task_id: mine.task, connector_id: mine.connector, secret_id: mine.secret, input_id: randomUUID(), user_id: (await admin(`/users?email=${encodeURIComponent(env.GEN9_SEED_USER_EMAIL)}&exact=true`))[0].id, source_id: randomUUID() };
  const requests = [];
  for (const [template, operations] of Object.entries(spec.paths)) {
    if (template.startsWith("/v1/me/plugins") || !template.startsWith("/v1/")) continue;
    for (const [method, op] of Object.entries(operations)) {
      const path = template.replace(/\{(\w+)\}/g, (_, name) => ids[name] ?? randomUUID());
      const query = (op.parameters ?? []).filter((p) => p.in === "query" && p.required).map((p) => `${p.name}=${encodeURIComponent(sample(p.schema, spec))}`);
      const url = query.length ? `${path}?${query.join("&")}` : path;
      const schema = op.requestBody?.content?.["application/json"]?.schema;
      const raw = op.requestBody && !schema ? "x" : undefined;
      requests.push({ method: method.toUpperCase(), template, url, body: schema ? sample(schema, spec) : undefined, raw, open: template === "/v1/healthz" || template.startsWith("/v1/readyz") });
    }
  }
  // Deletes last, so a hole in one can't hide the others
  requests.sort((x, y) => (x.method === "DELETE") - (y.method === "DELETE"));

  // The same URLs work for their owner: a refusal below is authorization, not a wrong URL
  const owned = requests.filter((r) => r.method === "GET" && /\{\w+_id\}/.test(r.template) && !r.template.startsWith("/v1/admin"));
  const broken = [];
  for (const r of owned) {
    const got = await call(await token(seeded), "GET", r.url, r);
    if (got.status !== 200) broken.push(`${r.template} ${got.status}`);
  }
  check(!broken.length, `the seeded user gets 200 on all ${owned.length} GET routes with their own ids`, broken.join("; "));

  // 1. No token
  const loose = [];
  for (const r of requests.filter((r) => !r.open)) {
    const got = await call(null, r.method, r.url, r);
    if (got.status !== 401) loose.push(`${r.method} ${r.template} ${got.status}`);
  }
  check(!loose.length, `without a token, all ${requests.filter((r) => !r.open).length} routes answer 401`, loose.join("; "));

  // 2. Admin routes as a person who isn't an admin
  const b = await token(other);
  const opened = [];
  for (const r of requests.filter((r) => r.template.startsWith("/v1/admin"))) {
    const got = await call(b, r.method, r.url, r);
    if (got.status !== 403) opened.push(`${r.method} ${r.template} ${got.status}`);
  }
  check(!opened.length, `every admin route (${requests.filter((r) => r.template.startsWith("/v1/admin")).length}) refuses a person who isn't an admin with 403`, opened.join("; "));

  // 3. The seeded user's ids, as the second person
  const leaks = [];
  const unclear = [];
  let tried = 0;
  // /fire takes a task's own token, not a person's: checked on its own below
  for (const r of requests.filter((r) => /\{\w+_id\}/.test(r.template) && !r.template.startsWith("/v1/admin") && !r.template.endsWith("/fire"))) {
    tried++;
    const got = await call(await token(other), r.method, r.url, r);
    if (got.status === 422) unclear.push(`${r.method} ${r.template}: ${got.text.slice(0, 120)}`);
    else if (![403, 404].includes(got.status)) leaks.push(`${r.method} ${r.template} ${got.status} ${got.text.slice(0, 80)}`);
  }
  inconclusive = unclear.length;
  check(!leaks.length, `another person gets 403 or 404 on all ${tried} routes with the seeded user's ids`, leaks.join("; "));
  if (unclear.length) console.log(`note  ${unclear.length} answered 422 before any check could run:\n      ${unclear.join("\n      ")}`);
  const fired = await call(await token(other), "POST", `/v1/tasks/${mine.task}/fire`, { body: {} });
  check(fired.status === 401, "a task's /fire refuses another person's access token (it takes only its own)", String(fired.status));

  // Still all the seeded user's
  a = await token(seeded);
  const still = [
    (await call(a, "GET", `/v1/threads/${mine.thread}`)).status,
    (await call(a, "GET", `/v1/threads/${mine.thread}/files/${mine.file}`)).status,
    (await call(a, "GET", "/v1/tasks")).json?.some((t) => t.id === mine.task) ? 200 : 404,
    (await call(a, "GET", "/v1/me/environment-secrets")).json?.some((s) => s.id === mine.secret) ? 200 : 404,
    (await call(a, "GET", "/v1/me/connectors")).json?.some((c) => c.id === mine.connector) ? 200 : 404,
  ];
  check(still.every((s) => s === 200), "afterwards the seeded user's chat, file, task, secret and connector are all still there, and theirs", still.join(", "));
  const runs = (await call(a, "GET", `/v1/threads/${mine.thread}/runs`)).json ?? [];
  check(runs.length === 1, "and their chat holds only their own run", `${runs.length} run(s)`);
} catch (e) {
  check(false, "the authorization check ran to the end", e.stack ?? String(e));
} finally {
  try {
    const a = await token(seeded);
    if (mine.task) await call(a, "DELETE", `/v1/tasks/${mine.task}`);
    if (mine.secret) await call(a, "DELETE", `/v1/me/environment-secrets/${mine.secret}`);
    if (mine.connector) await call(a, "DELETE", `/v1/me/connectors/${mine.connector}`);
    const left = await deleteChats(seeded, [mine.thread]);
    check(!left.length, "the seeded user's things it made are deleted", left.join(", "));
  } catch (e) {
    check(false, "cleanup", String(e));
  }
  if (otherId) await admin(`/users/${otherId}`, { method: "DELETE" }).catch(() => {});
  forget(otherId);
  rmSync(seeded, { recursive: true, force: true });
  rmSync(other, { recursive: true, force: true });
}
console.log(failures ? `\n${failures} check(s) failed` : `\nall authorization checks passed${inconclusive ? ` (${inconclusive} inconclusive)` : ""}`);
process.exit(failures ? 1 : 0);
