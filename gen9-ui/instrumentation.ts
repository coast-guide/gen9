/**
 * Next.js calls `register` once when a server starts, before it serves anything. A setting that is
 * missing or wrong stops it there, saying which and why (instrumentation-node.ts): otherwise the
 * server says it's ready and every request fails with nothing in the log (its health check blamed
 * the session store). Not during `next build`, which runs without the settings, nor in the edge
 * runtime.
 */
export async function register() {
  if (process.env.NEXT_RUNTIME === "nodejs" && process.env.NEXT_PHASE !== "phase-production-build") {
    await import("./instrumentation-node");
  }
}
