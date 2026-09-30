"use client";

import { useEffect, useState, useSyncExternalStore } from "react";

const noop = () => () => {};
const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["day", 86_400_000],
  ["hour", 3_600_000],
  ["minute", 60_000],
];

/**
 * "5 minutes ago", or "in 3 hours", in the viewer's locale; "just now" under a minute. The server renders the
 * absolute time (UTC) first, and the browser replaces it after hydration, like LocalDate. It moves on every
 * 30 seconds, so a page left open doesn't keep saying "in under a minute" after the time has passed.
 */
export function RelativeTime({ ms }: { ms: number }) {
  const hydrated = useSyncExternalStore(noop, () => true, () => false);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(timer);
  }, []);
  const date = new Date(ms);
  let text = date.toISOString().slice(0, 16).replace("T", " ");
  if (hydrated) {
    // Past ("5 minutes ago") or future ("in 3 hours": a scheduled task's next run)
    const elapsed = now - ms;
    const distance = Math.abs(elapsed);
    const [unit, size] = UNITS.find(([, size]) => distance >= size) ?? [null, 0];
    text = unit
      ? new Intl.RelativeTimeFormat(undefined, { numeric: "auto" }).format((elapsed > 0 ? -1 : 1) * Math.floor(distance / size), unit)
      : elapsed > 0
        ? "just now"
        : "in under a minute";
  }
  return (
    <time dateTime={date.toISOString()} title={hydrated ? date.toLocaleString() : undefined} suppressHydrationWarning>
      {text}
    </time>
  );
}
