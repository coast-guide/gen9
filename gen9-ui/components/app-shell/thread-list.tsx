"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import type { Thread } from "@/lib/agent";
import { cn } from "@/lib/utils";

const THREADS_EVENT = "gen9:threads";
let latest = 0;

/** Fetch the person's chats and show them in each thread list on the page, without re-rendering
 * the page: a chat asks this during and after a run ("Needs you", a new title), where a router
 * refresh would mount a new chat's page anew, losing what the person typed into a card and
 * reloading a connector's View. */
export async function refreshThreadList() {
  const mine = ++latest;
  const response = await fetch("/api/threads", { cache: "no-store" }).catch(() => null);
  const threads: unknown = response?.ok ? await response.json().catch(() => null) : null;
  // Only the latest answer: an earlier one arriving late would show an older list
  if (mine === latest && Array.isArray(threads)) window.dispatchEvent(new CustomEvent(THREADS_EVENT, { detail: threads }));
}

/** Calls `listener` with each list a refresh brings; returns how to stop listening. */
export function onThreads(listener: (threads: Thread[]) => void): () => void {
  const handle = (event: Event) => listener((event as CustomEvent<Thread[]>).detail);
  window.addEventListener(THREADS_EVENT, handle);
  return () => window.removeEventListener(THREADS_EVENT, handle);
}

export function ThreadList({ threads: rendered }: { threads: Thread[] | null }) {
  const pathname = usePathname();
  // The list the server rendered, until a refresh brings a newer one
  const [threads, setThreads] = useState(rendered);
  const [renderedBefore, setRenderedBefore] = useState(rendered);
  if (rendered !== renderedBefore) {
    setRenderedBefore(rendered);
    setThreads(rendered);
  }
  useEffect(() => onThreads(setThreads), []);
  if (threads === null) {
    return <p className="px-3 py-2 text-sm text-muted-foreground">Chats are unavailable right now.</p>;
  }
  if (threads.length === 0) {
    return <p className="px-3 py-2 text-sm text-muted-foreground">Your chats will appear here.</p>;
  }
  return (
    <ul className="flex min-w-0 flex-col gap-0.5">
      {threads.map((thread) => {
        const href = `/chat/${thread.id}`;
        const active = pathname === href;
        return (
          <li key={thread.id}>
            <Link
              href={href}
              aria-current={active ? "page" : undefined}
              title={thread.title}
              className={cn(
                "flex items-center gap-2 rounded-xl px-3 py-2 text-sm transition-colors pointer-coarse:py-2.5",
                "hover:bg-sidebar-accent aria-[current=page]:bg-sidebar-accent aria-[current=page]:font-medium",
              )}
            >
              <span dir="auto" className="min-w-0 flex-1 truncate">{thread.title}</span>
              {/* Its run asked something and waits for the person (docs/design/components.md, "Chat status") */}
              {thread.run_status === "waiting" && (
                <span className="flex shrink-0 items-center gap-1.5 text-xs font-medium">
                  <span className="size-1.5 rounded-full bg-leaf" aria-hidden />
                  Needs you
                </span>
              )}
            </Link>
          </li>
        );
      })}
    </ul>
  );
}
