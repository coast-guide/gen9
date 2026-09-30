// Why gen9-agent refused, in words for the person. Its own refusals are sentences (`detail`); a
// request that doesn't fit its schema gets FastAPI's list of problems instead, where a
// "Value error, …" is Gen9's own words (gen9-agent's not_blank.py) and the rest are for developers.
// Shown as it came, a list read "[object Object]" in Scheduled (docs/plans/gen9-learn.md, M9, F8).

const OURS = "Value error, ";
export const CHECK_INPUT = "Check what you entered and try again.";

/** The refusal to show: the API's sentence, its own words from a list, else `otherwise`. */
export function refusal(detail: unknown, otherwise: string): string {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const ours = detail.map((problem) => (problem as { msg?: unknown } | null)?.msg).find((msg): msg is string => typeof msg === "string" && msg.startsWith(OURS));
    return ours ? ours.slice(OURS.length) : CHECK_INPUT;
  }
  return otherwise;
}
