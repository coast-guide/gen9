"use client";

import { useEffect, useState, useSyncExternalStore } from "react";

import { relativeText, type Tense } from "@/lib/relative-time";

const noop = () => () => {};

/**
 * "5 minutes ago", or "in 3 hours", in the viewer's locale; "just now" under a minute. `tense` says
 * which side of now the time belongs (a task's next run is the one future time): one on the other side,
 * from a viewer's clock that is off, reads as now (lib/relative-time.ts). The server renders the
 * absolute time (UTC) first, and the browser replaces it after hydration, like LocalDate. It moves on
 * every 30 seconds, so a page left open doesn't keep saying "in under a minute" after the time has passed.
 */
export function RelativeTime({ ms, tense = "past" }: { ms: number; tense?: Tense }) {
  const hydrated = useSyncExternalStore(noop, () => true, () => false);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(timer);
  }, []);
  const date = new Date(ms);
  const text = hydrated ? relativeText(ms, now, tense) : date.toISOString().slice(0, 16).replace("T", " ");
  return (
    <time dateTime={date.toISOString()} title={hydrated ? date.toLocaleString() : undefined} suppressHydrationWarning>
      {text}
    </time>
  );
}
