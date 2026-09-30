"use client";

import { useSyncExternalStore } from "react";

const noop = () => () => {};

/**
 * A date in the viewer's own time zone and locale. The server doesn't know the viewer's zone,
 * so it renders an ISO date (UTC) first and the browser replaces it after hydration.
 */
export function LocalDate({ ms, withTime = false }: { ms: number; withTime?: boolean }) {
  const hydrated = useSyncExternalStore(noop, () => true, () => false);
  const date = new Date(ms);
  const style: Intl.DateTimeFormatOptions = withTime ? { dateStyle: "medium", timeStyle: "short" } : { dateStyle: "medium" };
  const iso = date.toISOString();
  return (
    <time dateTime={iso} suppressHydrationWarning>
      {hydrated ? new Intl.DateTimeFormat(undefined, style).format(date) : withTime ? `${iso.slice(0, 16).replace("T", " ")} UTC` : iso.slice(0, 10)}
    </time>
  );
}
