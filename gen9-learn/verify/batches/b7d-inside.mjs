// Batch 10, deeper, after b7: inside each stack, when things fail, and the operator's tools.
//   temporal   payloads encrypted in histories; the codec endpoint and the public frontend refuse
//              callers without a token; the web UI turns away a person who isn't an admin (Alan,
//              the seeded user: Puppeteer types his password, as make e2e does)
//   router     the aliases a key sees, the fallback, the admin API refusing a caller without a key
//   postgres   its extensions, the two roles and what the services' one may do
//   langfuse   its health, and ClickHouse's tables
//   sandbox    the server's settings and its disk watchdog
//   failures   NUL characters (422), an oversized body (413), the pages' security headers; an account
//              deleted while Langfuse is down (202, "being deleted", finished once it's back)
//   toolbox    make doctor, gen9-agent-reseal --check, make stop-agents and resume-agents (each in the
//              audit record), the containers' log limits, Docker's disk use
// No model call. Nothing here prints a secret: keys are read by the commands that use them.
import { randomBytes } from "node:crypto";
import { API, APP, admin, appdb, check, kcdb, kcEnv, readEnv, sh, signInWithPassword } from "../lib.mjs";

export default async function inside(ctx) {
  const { browser } = ctx;
  const obs = {};
  const temporal = (args) => sh(`docker compose -f gen9-temporal/compose.yaml run --rm cli temporal ${args} 2>/dev/null`, { timeout: 90_000 }).out;

  // ---- temporal: encrypted payloads, token-only frontend, admins-only UI
  let latest = null;
  try {
    latest = JSON.parse(temporal(`workflow list --query "WorkflowType='SweepDeletedUsersWorkflow' AND ExecutionStatus='Completed'" --limit 1 -o json`))[0];
  } catch {}
  let encodings = [];
  try {
    const events = JSON.parse(temporal(`workflow show -w ${latest.execution.workflowId} -r ${latest.execution.runId} -o json`)).events ?? [];
    encodings = events
      .flatMap((e) => Object.values(e).filter((v) => v && typeof v === "object"))
      .flatMap((a) => [a.input, a.result].filter(Boolean))
      .flatMap((p) => p.payloads ?? [])
      .map((p) => Buffer.from(p.metadata?.encoding ?? "", "base64").toString());
  } catch {}
  obs.payloadEncodings = [...new Set(encodings)];
  const codec = await fetch(`${API}/v1/temporal/codec/decode`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ payloads: [] }) });
  obs.codecWithoutToken = codec.status;
  obs.frontendWithoutToken = sh(
    "docker compose -f gen9-temporal/compose.yaml run --rm --entrypoint sh cli -c 'env -u TEMPORAL_TLS_SERVER_CA_CERT_DATA -u TEMPORAL_TLS_SERVER_CERT_DATA -u TEMPORAL_TLS_SERVER_KEY_DATA -u TEMPORAL_TLS_INTERNODE_SERVER_NAME temporal workflow list --address temporal:7233 --namespace gen9 --limit 1' 2>&1 | grep '^Error'",
    { timeout: 90_000 },
  ).out;
  check(
    obs.payloadEncodings.length > 0 && obs.payloadEncodings.every((e) => e === "binary/encrypted") && obs.codecWithoutToken === 401 && /Request unauthorized/.test(obs.frontendWithoutToken),
    "Temporal holds payloads encrypted; its codec endpoint and its public frontend refuse a caller without a token",
    `${latest?.type?.name}: ${obs.payloadEncodings.join(", ")}; codec ${obs.codecWithoutToken}; frontend: ${obs.frontendWithoutToken}`,
  );
  const visitor = await browser.createBrowserContext();
  try {
    const ui = await visitor.newPage();
    await ui.goto("http://localhost:18000/namespaces/gen9/workflows", { waitUntil: "networkidle0" });
    await Promise.all([ui.waitForNavigation({ waitUntil: "networkidle0" }), ui.locator("button ::-p-text(Continue to SSO)").click()]);
    obs.uiClient = new URL(ui.url()).searchParams.get("client_id");
    await ui.locator("#username").fill(kcEnv.GEN9_SEED_USER_EMAIL);
    await ui.type("#password", kcEnv.GEN9_SEED_USER_PASSWORD);
    await Promise.all([ui.waitForNavigation({ waitUntil: "networkidle0" }), ui.click("#kc-login")]);
    obs.uiRefusal = { heading: await ui.$eval("h1", (h) => h.textContent.trim()).catch(() => ""), text: (await ui.evaluate(() => document.body.innerText)).match(/Temporal is for Gen9 admins[^\n]*/)?.[0] ?? "" };
    check(obs.uiClient === "temporal-ui" && /Temporal is for Gen9 admins/.test(obs.uiRefusal.text), "Temporal's web UI signs in through Keycloak (client temporal-ui), which turns away a person who isn't an admin, in Gen9's words", `${obs.uiRefusal.heading}: ${obs.uiRefusal.text}`);
  } finally {
    await visitor.close();
  }

  // ---- router: what a key sees, the fallback, the admin API
  const agentKey = readEnv("gen9-agent/models.local.env").GEN9_MODELS_KEY;
  const apiKey = readEnv("gen9-agent/models-api.local.env").GEN9_MODELS_KEY;
  const models = async (key) => (await (await fetch("http://127.0.0.1:19000/v1/models", { headers: { Authorization: `Bearer ${key}` } })).json()).data?.map((m) => m.id).sort() ?? [];
  obs.workerSees = await models(agentKey);
  obs.apiSees = await models(apiKey);
  obs.fallback = sh("grep -E '^\\s+fallbacks:' gen9-models/config.yaml").out.trim();
  obs.adminWithoutKey = (await fetch("http://127.0.0.1:19001/users/nobody/usage")).status;
  check(
    obs.workerSees.includes("chat") && obs.workerSees.includes("embed") && obs.apiSees.join() === "embed,rerank" && obs.adminWithoutKey === 401,
    "the worker's key sees every alias, the API's only embed and rerank; the admin API refuses a caller without a key",
    `worker: ${obs.workerSees.join(", ")}; api: ${obs.apiSees.join(", ")}; ${obs.fallback}; admin API ${obs.adminWithoutKey}`,
  );

  // ---- postgres: extensions, roles, what the services' role may do
  obs.extensions = appdb("select string_agg(extname || ' ' || extversion, ', ' order by extname) from pg_extension");
  obs.roles = appdb("select string_agg(rolname || case when rolsuper then ' (superuser)' else '' end, ', ' order by rolname) from pg_roles where rolname in ('postgres', 'gen9_agent', 'gen9_agent_app')");
  obs.owner = appdb("select tableowner from pg_tables where tablename = 'audit_events'");
  obs.appMay = appdb(
    "select string_agg(p || ' ' || has_table_privilege('gen9_agent_app', 'audit_events', p), ', ') from unnest(array['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE']) p",
  );
  check(/pg_textsearch/.test(obs.extensions) && /pg_trgm/.test(obs.extensions) && /vector/.test(obs.extensions) && obs.owner === "gen9_agent" && /SELECT true, INSERT true, UPDATE false, DELETE false, TRUNCATE false/.test(obs.appMay), "the App DB's extensions; gen9_agent owns the tables, and the services' role may only read and add audit rows", `${obs.extensions}; ${obs.roles}; gen9_agent_app on audit_events: ${obs.appMay}`);

  // ---- langfuse: health, and ClickHouse's tables
  obs.langfuseHealth = await fetch("http://localhost:13000/api/public/health").then((r) => r.text()).catch((e) => e.message);
  obs.workerHealth = await fetch("http://localhost:13003/api/health").then((r) => `${r.status} ${r.statusText}`).catch((e) => e.message);
  obs.clickhouseTables = sh(`docker exec gen9-langfuse-clickhouse-1 clickhouse-client -q "select name from system.tables where database = 'default' order by name"`).out.split("\n").join(", ");
  check(/OK/.test(obs.langfuseHealth) && /200/.test(obs.workerHealth) && /events_core/.test(obs.clickhouseTables), "Langfuse's web and worker answer their health checks; ClickHouse holds its tables", `${obs.langfuseHealth.slice(0, 80)}; worker ${obs.workerHealth}; ${obs.clickhouseTables}`);

  // ---- sandbox: the server's settings and its disk watchdog
  obs.sandboxConfig = sh("docker exec gen9-sandbox-opensandbox-1 sh -c \"grep -E '^\\[|timeout|image|network' /etc/opensandbox/config.toml\"").out.split("\n").slice(0, 16).join(" | ");
  obs.sandboxLog = sh("docker logs gen9-sandbox-opensandbox-1 2>&1 | grep -iE 'disk|watch' | tail -2").out.replace(/\s+/g, " ");
  check(obs.sandboxConfig.length > 0, "the sandbox server's settings (config.toml) and its disk watchdog", `${obs.sandboxConfig.slice(0, 200)}; ${obs.sandboxLog.slice(0, 160)}`);

  // ---- failures: what the API answers, and the pages' headers
  obs.nul = sh(`curl -s -w ' %{http_code}' -X POST ${API}/v1/threads -H 'Content-Type: application/json' --data-binary '{"a":"\\u0000"}'`).out;
  obs.big = sh(`head -c 20000000 /dev/zero | curl -s -o /dev/null -w '%{http_code}' -X POST ${API}/v1/threads -H 'Content-Type: application/json' --data-binary @-`).out;
  obs.uiHeaders = sh(`curl -sI ${APP}/ | grep -iE '^(content-security-policy|x-frame-options|x-content-type-options|referrer-policy|cross-origin-opener-policy|permissions-policy):' | cut -d: -f1 | tr 'A-Z' 'a-z' | sort | tr '\\n' ' '`).out.trim();
  obs.apiHeaders = sh(`curl -sI ${API}/healthz | grep -iE '^(content-security-policy|x-frame-options|x-content-type-options|cache-control):' | tr -d '\\r'`).out.split("\n").join("; ");
  check(/U\+0000.* 422$/.test(obs.nul) && obs.big === "413" && /content-security-policy/.test(obs.uiHeaders), "a NUL character gets 422 with why, an oversized body 413; every page carries its security headers", `${obs.nul}; big body ${obs.big}; gen9-ui: ${obs.uiHeaders}; API: ${obs.apiHeaders}`);

  // ---- failures: Langfuse down while an account is deleted. The deletion waits on erasing the traces,
  // so the API answers 202 after 15 s and the person is told it is under way; it finishes on its own
  // once Langfuse is back (M9, F22 and F25). A throwaway account made through Keycloak's admin API;
  // its password is made here and typed by Puppeteer, never printed
  const gone = { email: `gone-${Date.now()}@gen9.test`, password: `${randomBytes(18).toString("base64url")}Aa1!` };
  await admin("/users", { method: "POST", body: JSON.stringify({ username: gone.email, email: gone.email, emailVerified: true, enabled: true, firstName: "Gone", lastName: "Soon",
    credentials: [{ type: "password", value: gone.password, temporary: false }] }) });
  gone.sub = kcdb(`select u.id from user_entity u join realm r on r.id = u.realm_id where r.name = 'gen9' and u.email = '${gone.email}'`);
  const goner = await (await browser.createBrowserContext()).newPage();
  await signInWithPassword(goner, gone.email, gone.password);
  await goner.goto(`${APP}/settings?delete=1`, { waitUntil: "networkidle0" });
  await goner.waitForSelector("#delete-confirmation");
  await goner.type("#delete-confirmation", gone.email);
  sh("cd gen9-langfuse && docker compose stop langfuse-web");
  try {
    const clicked = Date.now();
    for (const b of await goner.$$("[role=alertdialog] button")) if ((await b.evaluate((el) => el.textContent.trim())) === "Delete account") await b.click();
    await goner.waitForFunction(() => location.pathname === "/signed-out", { timeout: 60_000 });
    obs.langfuseDownDelete = { s: Math.round((Date.now() - clicked) / 1000), url: goner.url().replace(APP, ""), title: await goner.$eval("h1", (h) => h.textContent), rows: appdb(`select count(*) from users where sub = '${gone.sub}'`) };
  } finally {
    sh("cd gen9-langfuse && docker compose start langfuse-web");
  }
  // Retries back off from 5 s: after about 20 s down, the next try comes within a minute of Langfuse's return
  for (let i = 0; i < 60 && kcdb(`select count(*) from user_entity where id = '${gone.sub}'`) !== "0"; i++) await new Promise((resolve) => setTimeout(resolve, 3000));
  obs.langfuseDownDelete.after = { keycloak: kcdb(`select count(*) from user_entity where id = '${gone.sub}'`), rows: appdb(`select count(*) from users where sub = '${gone.sub}'`) };
  check(obs.langfuseDownDelete.url === "/signed-out?reason=deleting" && obs.langfuseDownDelete.title === "Your account is being deleted." && obs.langfuseDownDelete.rows === "1" &&
    obs.langfuseDownDelete.after.keycloak === "0" && obs.langfuseDownDelete.after.rows === "0",
    "Langfuse down: deleting an account answers after 15 s that it is being deleted, its data still there; once Langfuse is back it finishes on its own", JSON.stringify(obs.langfuseDownDelete));
  await goner.browserContext().close();

  // ---- toolbox
  const doctor = sh("make doctor 2>&1 | tail -4", { timeout: 120_000 });
  obs.doctor = doctor.out.replace(/\s+/g, " ");
  const reseal = sh("cd gen9-agent && docker compose exec -T worker gen9-agent-reseal --check 2>&1 | tail -4", { timeout: 120_000 });
  obs.reseal = reseal.out.replace(/\s+/g, " ");
  check(doctor.code === 0 && reseal.code === 0 && /deletion(\(s\))? (has been )?running for over a day/.test(obs.doctor),
    "make doctor passes, and says whether a deletion has run for over a day; gen9-agent-reseal --check counts the sealed values under each key, changing nothing", `${obs.doctor.slice(0, 160)}; ${obs.reseal.slice(0, 160)}`);
  const since = new Date().toISOString();
  const stop = sh("YES=1 make stop-agents 2>&1 | tail -3", { timeout: 300_000 });
  obs.stop = stop.out.replace(/\s+/g, " ");
  obs.workerWhileStopped = sh("docker ps -a --filter name=gen9-agent-worker-1 --format '{{.Status}}'").out;
  const resume = sh("YES=1 make resume-agents 2>&1 | tail -3", { timeout: 300_000 });
  obs.resume = resume.out.replace(/\s+/g, " ");
  obs.workerAfter = sh("docker ps --filter name=gen9-agent-worker-1 --format '{{.Status}}'").out;
  obs.operatorAudit = appdb(`select string_agg(action || ' ' || outcome, ', ' order by id) from audit_events where action like 'operator.%' and at >= '${since}'`);
  check(
    stop.code === 0 && /Exited/.test(obs.workerWhileStopped) && resume.code === 0 && /Up/.test(obs.workerAfter) && /operator\.stop success.*operator\.resume success/.test(obs.operatorAudit),
    "make stop-agents stops the worker and resume-agents starts it again, each in the audit record",
    `${obs.stop.slice(0, 120)} | ${obs.workerWhileStopped} | ${obs.resume.slice(0, 120)} | ${obs.operatorAudit}`,
  );
  obs.logConfig = sh("docker inspect gen9-agent-api-1 --format '{{.HostConfig.LogConfig.Type}} {{json .HostConfig.LogConfig.Config}}'").out;
  obs.disk = sh("docker system df --format '{{.Type}}: {{.Size}} ({{.Reclaimable}} reclaimable)'").out.split("\n").join("; ");
  check(/^local /.test(obs.logConfig), "each container's log is bounded (Docker's local driver); docker system df says what the disk holds", `${obs.logConfig}; ${obs.disk}`);
  return obs;
}
