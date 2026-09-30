import * as z from "zod";

// Zod 4 checks whether it may compile parsers with `new Function`; under this app's CSP (no
// 'unsafe-eval') the check throws, which Zod swallows, but the browser still reports a violation on
// every page that loads it, noise in what app/api/csp-report logs. Zod's `jitless` skips the check
// ("strict CSPs report the caught `new Function`", zod/v4/core/util.js). Imported first by whatever
// brings Zod to the browser (the MCP Apps host SDK), before any schema runs (manual-e2e.md, P3-F3).
z.config({ jitless: true });
