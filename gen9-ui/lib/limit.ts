/** How much of their model usage limit a person has used, and when it resets (gen9-agent's
 * `GET /v1/me/limit`; docs/plans/manual-e2e.md, P5-D1). */
export type Limit = { limited: boolean; used: number | null; resets_at: string | null };

const day = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });

/** "3% of your limit, which resets on 1 October 2026". */
export function limitWords(limit: Limit): string {
  if (!limit.limited) return "No limit";
  const used = limit.used ?? 0;
  const share = used >= 1 ? "All of your limit" : used < 0.01 ? "Less than 1% of your limit" : `${Math.round(used * 100)}% of your limit`;
  if (!limit.resets_at) return share;
  const when = day.format(new Date(limit.resets_at));
  return used >= 1 ? `${share}: it resets on ${when}` : `${share}, which resets on ${when}`;
}
