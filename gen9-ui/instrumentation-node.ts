import { envProblems } from "./lib/env";

// The settings check at start (instrumentation.ts), in its own file so only the Node.js server
// loads it: process.exit isn't in the edge runtime (Next.js's instrumentation guide)
const problems = envProblems();
if (problems.length) {
  for (const problem of problems) console.error(`[settings] ${problem}`);
  console.error("[settings] gen9-ui can't start: fix these in gen9-ui/.env (make setup STACKS=ui writes it; docs/secrets.md says how to replace each)");
  process.exit(1);
}
