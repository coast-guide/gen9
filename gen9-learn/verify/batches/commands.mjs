// Every command in index.html marked data-check="run" (must print something) or "run-any" (may print
// nothing yet, e.g. a log search for an event later in the story), run as a reader would paste it (from the
// repo root), with trace@gen9.test replaced by this run's user. Runs after b2, while the user has a
// web session, a chat and a trace. Prints pass or fail per command, never the output: some commands
// print the reader's own passwords.
import { readFileSync } from "node:fs";
import { ROOT, check, sh } from "../lib.mjs";

const decode = (s) => s.replaceAll("&lt;", "<").replaceAll("&gt;", ">").replaceAll("&quot;", '"').replaceAll("&#39;", "'").replaceAll("&amp;", "&");

export default async function commands(ctx) {
  const page = readFileSync(`${ROOT}gen9-learn/index.html`, "utf8");
  const all = [...page.matchAll(/<code data-check="(run|run-any|manual)">([\s\S]*?)<\/code>/g)].map((m) => ({ kind: m[1], command: decode(m[2]) }));
  const runnable = all.filter((c) => c.kind !== "manual");
  const obs = { total: all.length, run: runnable.length, manual: all.length - runnable.length, failed: [] };
  for (const [i, { kind, command }] of runnable.entries()) {
    const result = sh(command.replaceAll("trace@gen9.test", ctx.user.email), { timeout: 90_000 });
    const label = `command ${i + 1}: ${command.split("\n")[0].slice(0, 70)}`;
    if (!check(result.code === 0 && (kind === "run-any" || result.out.length > 0), label, result.code === 0 ? "" : `exit ${result.code}`)) obs.failed.push(command.slice(0, 120));
  }
  return obs;
}
