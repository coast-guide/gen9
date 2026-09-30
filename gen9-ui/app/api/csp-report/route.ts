import { readBounded } from "@/lib/bounded-body";
import { line, MAX_REPORT_BYTES, violations } from "@/lib/csp-report";

export const dynamic = "force-dynamic";

// A browser reports each blocked load; a page that keeps tripping, or anyone posting here, could
// flood the log: at most this many lines a minute per server, then one line saying how many more
const LINES_PER_MINUTE = 60;
let minute = 0;
let logged = 0;
let dropped = 0;

/**
 * Where browsers report what gen9-ui's Content-Security-Policy blocked (proxy.ts: `report-to`,
 * with `report-uri` for older browsers), so whoever runs Gen9 learns of it: a page the policy
 * breaks, or an injection it stopped (manual-e2e.md, P3-F3). Logged one line per violation, by
 * origin and path only; nothing is stored. Browsers send reports without cookies, so this takes
 * any caller, bounded.
 */
export async function POST(request: Request) {
  const contentType = request.headers.get("content-type") ?? "";
  const length = Number(request.headers.get("content-length") ?? 0);
  if (length > MAX_REPORT_BYTES) return new Response(null, { status: 413 });
  // Bounded as it's read: a chunked body has no length to check first
  const raw = await readBounded(request.body, MAX_REPORT_BYTES);
  if (raw === null) return new Response(null, { status: 413 });
  let body: unknown;
  try {
    body = JSON.parse(raw);
  } catch {
    return new Response(null, { status: 400 });
  }
  const now = Math.floor(Date.now() / 60_000);
  if (now !== minute) {
    if (dropped) console.warn(`[csp] ${dropped} more reports in the last minute, not logged`);
    minute = now;
    logged = 0;
    dropped = 0;
  }
  for (const violation of violations(contentType, body)) {
    if (logged < LINES_PER_MINUTE) {
      logged++;
      console.warn(line(violation));
    } else {
      dropped++;
    }
  }
  return new Response(null, { status: 204 });
}
