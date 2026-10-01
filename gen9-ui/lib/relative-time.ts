/** "5 minutes ago", or "in 3 hours", in the viewer's locale (components/relative-time.tsx). */
export type Tense = "past" | "future";

const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["day", 86_400_000],
  ["hour", 3_600_000],
  ["minute", 60_000],
];

/**
 * How long ago `ms` was, or how long until it, at `now`. A time on the wrong side of now for its tense
 * reads as now: with a viewer's clock a few minutes off, something just done read "in 3 minutes"
 * (docs/plans/manual-e2e.md, P5-D3 and P6-E1), as it would without GitHub's relative-time element's
 * `tense`, which does the same.
 */
export function relativeText(ms: number, now: number, tense: Tense, locale?: string): string {
  const elapsed = now - ms;
  if (tense === "past" && elapsed < 60_000) return "just now";
  if (tense === "future" && elapsed > -60_000) return elapsed > 0 ? "now" : "in under a minute";
  const distance = Math.abs(elapsed);
  const [unit, size] = UNITS.find(([, size]) => distance >= size) ?? UNITS[UNITS.length - 1];
  return new Intl.RelativeTimeFormat(locale, { numeric: "auto" }).format((tense === "past" ? -1 : 1) * Math.floor(distance / size), unit);
}
