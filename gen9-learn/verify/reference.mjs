// Checks that the page names everything the running system has (docs/plans/gen9-learn.md, M7): each
// stack's services and volumes, Temporal's workflow types, Activities and Schedules, the API's
// operations, the MCP server's tools, the App DB's tables, the shared networks, the make commands,
// the web app's routes, gen9-agent's settings and every settings file's keys, the terminal's and
// gen9-agent's commands, that every link within the page leads somewhere, and that every pointer
// into the code lands on what it names. Reads the system itself (Compose, the API's OpenAPI
// document, Temporal, Postgres, the Makefile, the code and the settings files' key names), so
// anything added later fails here until the page says what it is.
//
//   node reference.mjs            each kind: how many the system has, and any the page doesn't name
// Needs the stacks up. Prints names only, never a value.
import { readFileSync, readdirSync } from "node:fs";
import { API, ROOT, appdb, check, failed, sh } from "./lib.mjs";

const page = readFileSync(`${ROOT}gen9-learn/index.html`, "utf8").replaceAll("&amp;", "&").replaceAll("&lt;", "<").replaceAll("&gt;", ">");
// A name counts when the page has it as code (<code>…</code>) or as a word anywhere in the text
const names = (list) => [...new Set(list)].filter(Boolean).sort();
const missing = (list, has = (n) => page.includes(n)) => list.filter((n) => !has(n));

function report(kind, list, has) {
  const gone = missing(list, has);
  check(gone.length === 0, `${kind}: ${list.length}, each named on the page`, gone.length ? `missing: ${gone.join(", ")}` : "");
  return gone;
}

// 1. Every service of every stack, as `<stack>` / `<service>` (Compose's own list, profiles included)
const COMPOSE_FILES = ["compose.yaml", "docker-compose.yml"];
const stacks = readdirSync(ROOT).filter((d) => d.startsWith("gen9-") && readdirSync(`${ROOT}${d}`).some((f) => COMPOSE_FILES.includes(f)));
const services = [];
for (const stack of stacks) {
  const out = sh(`cd ${stack} && docker compose --profile '*' config --services 2>/dev/null`).out;
  for (const service of out.split("\n").filter(Boolean)) services.push(`${stack}/${service}`);
}
// A service is named as "<stack> … <service>" in the atlas: its row names the stack and the service
report("Services", names(services), (n) => {
  const [stack, service] = n.split("/");
  return new RegExp(`data-service="${stack}/${service}"`).test(page);
});

// 2. Temporal: workflow classes and Activity names, from the workflow code (what the worker registers)
const code = readdirSync(`${ROOT}gen9-agent/src/gen9_agent/workflows`).filter((f) => f.endsWith(".py")).map((f) => readFileSync(`${ROOT}gen9-agent/src/gen9_agent/workflows/${f}`, "utf8")).join("\n");
report("Workflows", names([...code.matchAll(/class (\w+Workflow)\b/g)].map((m) => m[1])));
const activityNames = readFileSync(`${ROOT}gen9-agent/src/gen9_agent/workflows/names.py`, "utf8");
report("Activities", names([...activityNames.matchAll(/^[A-Z0-9_]+ = "([a-z0-9_]+)"$/gm)].map((m) => m[1]).filter((n) => !["answered", "acquire", "end"].includes(n))));

// 3. Temporal Schedules (a task's own are named task-<id>: one row, as "task-")
const schedules = sh("docker compose -f gen9-temporal/compose.yaml run --rm cli temporal schedule list -o json 2>/dev/null").out;
let scheduleIds = [];
try {
  scheduleIds = JSON.parse(schedules.slice(schedules.indexOf("["))).map((s) => s.scheduleId.replace(/^task-.*/, "task-"));
} catch {}
report("Schedules", names(scheduleIds));

// 4. The API: every operation of its OpenAPI document, as METHOD path
const openapi = await fetch(`${API}/openapi.json`).then((r) => r.json());
const operations = Object.entries(openapi.paths).flatMap(([path, ops]) => Object.keys(ops).map((m) => `${m.toUpperCase()} ${path}`));
report("API operations", names(operations), (n) => page.includes(`<code>${n}</code>`));

// 5. The MCP server's tools, from its code
const mcp = readFileSync(`${ROOT}gen9-agent/src/gen9_agent/mcp_server.py`, "utf8");
report("MCP tools", names([...mcp.matchAll(/@mcp\.tool\([^)]*\)\s*\n\s*async def (\w+)/g)].map((m) => m[1])));

// 6. The App DB's tables (gen9-postgres), the agent's and LangGraph's
const tables = appdb("select table_schema || '.' || table_name from information_schema.tables where table_schema in ('public', 'langgraph') and table_type = 'BASE TABLE' order by 1").split("\n").filter(Boolean).map((t) => t.replace(/^public\./, ""));
report("Tables", names(tables), (n) => page.includes(`<code>${n}</code>`));

// 7. The shared networks stacks meet on
report("Networks", names(sh("docker network ls --filter label=gen9.network=shared --format '{{.Name}}'").out.split("\n")));

// 8. The make commands
report("make commands", names([...readFileSync(`${ROOT}Makefile`, "utf8").matchAll(/^([a-z][a-z0-9-]*):/gm)].map((m) => m[1])), (n) => page.includes(`make ${n}`));

// 9. The web app's pages and route handlers (gen9-ui/app), as the browser asks for them:
// route groups such as (app) dropped, [[...id]] shown as it is in the folder
const routes = [];
const walk = (dir) => {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.isDirectory()) walk(`${dir}/${entry.name}`);
    else if (entry.name === "page.tsx" || entry.name === "route.ts") {
      const path = dir.slice(`${ROOT}gen9-ui/app`.length).split("/").filter((p) => p && !/^\(.*\)$/.test(p)).join("/");
      routes.push(`/${path}`);
    }
  }
};
walk(`${ROOT}gen9-ui/app`);
report("Web app routes", names(routes), (n) => page.includes(`<code>${n}</code>`));

// 10. gen9-agent's settings (settings.py), by the environment variable each is read from
const settingsCode = readFileSync(`${ROOT}gen9-agent/src/gen9_agent/settings.py`, "utf8");
report("gen9-agent settings", names([...settingsCode.matchAll(/^    ([a-z][a-z0-9_]*): /gm)].map((m) => m[1].toUpperCase())), (n) => page.includes(`<code>${n}</code>`));

// 11. Every stack's settings files (.env and the *.local.env one stack writes for another): their
// keys' names, read from this install's files (names only, never a value)
const envKeys = [];
for (const stack of stacks) {
  for (const file of readdirSync(`${ROOT}${stack}`).filter((f) => f === ".env" || f.endsWith(".local.env"))) {
    for (const m of readFileSync(`${ROOT}${stack}/${file}`, "utf8").matchAll(/^([A-Z][A-Z0-9_]*)=/gm)) envKeys.push(m[1]);
  }
}
report("Settings files' keys", names(envKeys), (n) => page.includes(`<code>${n}</code>`));
// …and what each of Gen9's own Compose files reads from its .env, with a default (${VAR:-…}): each
// stack's compose.yaml, and gen9-langfuse's compose.override.yaml; its docker-compose.yml is
// Langfuse's, documented by Langfuse
const composeVars = [];
for (const stack of stacks) {
  for (const file of readdirSync(`${ROOT}${stack}`).filter((f) => f === "compose.yaml" || f === "compose.override.yaml")) {
    for (const m of readFileSync(`${ROOT}${stack}/${file}`, "utf8").matchAll(/\$\{([A-Z][A-Z0-9_]*)/g)) composeVars.push(m[1]);
  }
}
report("Compose files' settings", names(composeVars), (n) => page.includes(`<code>${n}</code>`));

// 12. Every stack's named volumes, as Docker names them (Compose's resolved name: <project>_<volume>)
const volumes = [];
for (const stack of stacks) {
  const config = JSON.parse(sh(`cd ${stack} && docker compose --profile '*' config --format json 2>/dev/null`).out || "{}");
  for (const volume of Object.values(config.volumes ?? {})) volumes.push(volume.name);
}
report("Volumes", names(volumes), (n) => page.includes(`<code>${n}</code>`));

// 13. The terminal's commands (gen9-cli's argparse) and gen9-agent's own (its package's scripts)
const cli = readFileSync(`${ROOT}gen9-cli/src/gen9_cli/main.py`, "utf8");
report("gen9 commands", names([...cli.matchAll(/commands\.add_parser\(\s*"([a-z]+)"/g)].map((m) => m[1])), (n) => page.includes(`gen9 ${n}`));
const scripts = readFileSync(`${ROOT}gen9-agent/pyproject.toml`, "utf8").split("[project.scripts]")[1].split("\n[")[0];
report("gen9-agent commands", names([...scripts.matchAll(/^([a-z0-9-]+) = /gm)].map((m) => m[1])), (n) => page.includes(`<code>${n}</code>`));

// 14. Every link within the page leads somewhere on it
const ids = new Set([...page.matchAll(/ id="([^"]+)"/g)].map((m) => m[1]));
report("Links within the page", names([...page.matchAll(/href="#([^"]+)"/g)].map((m) => m[1])), (n) => ids.has(n));

// 15. Every pointer into the code (<code>path:line</code>, or :line after one, or a path under the
// last one's folders) still points at what it names: its data-at text on its first line, and a
// range's data-to on its last. Code moves as it changes, and a pointer a line off sends a reader
// to the wrong place without anything failing (M9: 6 of 33 had drifted). A failure says where the
// text is now.
const pointers = [];
let lastFile = null;
for (const m of page.matchAll(/<code([^>]*)>((?:[\w()./[\]-]+\.(?:py|ts|tsx|mjs|js|sh|sql|toml|ya?ml))?):(\d+)(?:-(\d+))?<\/code>/g)) {
  const [, attrs, path, from, to] = m;
  let file = path ? null : lastFile;
  if (path && path.startsWith("gen9-")) file = path;
  else if (path) {
    // Under the folders of the last full path, nearest first: runs/events.py after …/api/runs.py
    const parts = (lastFile ?? "").split("/");
    for (let i = parts.length - 1; i > 0 && !file; i--) {
      const candidate = `${parts.slice(0, i).join("/")}/${path}`;
      if (sh(`test -f '${ROOT}${candidate}' && echo yes`).out === "yes") file = candidate;
    }
  }
  if (path && file) lastFile = file;
  const attr = (name) => attrs.match(new RegExp(` ${name}="([^"]*)"`))?.[1];
  pointers.push({ shown: `${path}:${from}${to ? `-${to}` : ""}`, file, from: Number(from), to: to && Number(to), at: attr("data-at"), until: attr("data-to") });
}
const lines = new Map();
const wrong = [];
for (const p of pointers) {
  if (!p.file) {
    wrong.push(`${p.shown}: no such file`);
    continue;
  }
  if (!lines.has(p.file)) lines.set(p.file, readFileSync(`${ROOT}${p.file}`, "utf8").split("\n"));
  const text = lines.get(p.file);
  const where = (t) => text.flatMap((l, i) => (l.includes(t) ? [i + 1] : [])).join(", ") || "nowhere";
  if (!p.at) wrong.push(`${p.shown} (${p.file}): no data-at; the line is now "${text[p.from - 1]?.trim().slice(0, 70)}"`);
  else if (!text[p.from - 1]?.includes(p.at)) wrong.push(`${p.shown} (${p.file}): "${p.at}" is at ${where(p.at)}`);
  if (p.to && !p.until) wrong.push(`${p.shown} (${p.file}): a range with no data-to; its last line is now "${text[p.to - 1]?.trim().slice(0, 70)}"`);
  else if (p.to && !text[p.to - 1]?.includes(p.until)) wrong.push(`${p.shown} (${p.file}): its end "${p.until}" is at ${where(p.until)}`);
}
check(wrong.length === 0, `Pointers into the code: ${pointers.length}, each at what it names`, wrong.length ? `\n  ${wrong.join("\n  ")}` : "");

console.log(failed() ? `\n${failed()} kind(s) not complete` : "\nthe page names everything");
process.exit(failed() ? 1 : 0);
