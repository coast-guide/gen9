#!/usr/bin/env node
// Gen9's plugin loader (gen9-agent's plugins.py) against the Agent Plugins 1.0.0 conformance kit
// (agent-plugins-conformance-kit: 133 plugin folders, each with the load report the spec
// requires).
//
//   node plugins-conformance.mjs            the whole corpus; every fixture must pass
//   node plugins-conformance.mjs <folder>   the adapter: one folder's load report, as the kit wants
//
// No stack needs to run: it loads folders from disk, with gen9-agent's environment (synced
// first).
//
// The report is what the loader accepts. Gen9 then connects only to streamable-http servers and
// runs no plugin's processes; the kit assumes a client that runs stdio servers, and the plugin's
// page in Gen9 shows the rest as not connected.
import { execFileSync, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const agent = join(here, "..", "gen9-agent");

if (process.argv[2]) {
  const out = execFileSync(
    "uv",
    ["run", "--no-sync", "--project", agent, "python", "-m", "gen9_agent.plugins", process.argv[2]],
    { encoding: "utf8" },
  );
  const r = JSON.parse(out);
  console.log(
    JSON.stringify({
      rejected: r.rejected,
      loaded: {
        skills: r.skills.map((s) => s.name),
        mcpServers: r.mcp_servers.map((s) => s.name),
      },
      skipped: r.skipped.map((s) => ({ what: s.what })),
      reported: r.reported.map((s) => ({ field: s.field })),
    }),
  );
} else {
  execFileSync("uv", ["sync", "--locked", "--quiet", "--project", agent], { stdio: "inherit" });
  const kit = join(here, "node_modules", ".bin", "apconform");
  const run = spawnSync(
    kit,
    ["run", "--adapter", fileURLToPath(import.meta.url), "--strict-reporting", "--quiet"],
    { stdio: "inherit" },
  );
  process.exit(run.status ?? 1);
}
