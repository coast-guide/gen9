"use client";

import { useSyncExternalStore } from "react";

import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import type { AnswerSource, AnswerSources } from "@/lib/sources";

const WIDE = "(min-width: 640px)";
const subscribe = (change: () => void) => {
  const query = window.matchMedia(WIDE);
  query.addEventListener("change", change);
  return () => query.removeEventListener("change", change);
};

function SourceList({ title, sources }: { title: string; sources: AnswerSource[] }) {
  if (!sources.length) return null;
  return (
    <section className="grid min-w-0 gap-2">
      <h3 className="px-3 text-xs font-medium text-muted-foreground">{title}</h3>
      <ol className="grid min-w-0 gap-1">
        {sources.map((source) => (
          <li key={source.url} className="min-w-0">
            <a
              href={source.url}
              target="_blank"
              rel="noopener noreferrer nofollow"
              className="block overflow-hidden rounded-xl px-3 py-2 transition-colors hover:bg-muted focus-visible:bg-muted focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-hidden"
            >
              <span className="block truncate text-sm font-medium">{source.title || source.place}</span>
              <span className="block truncate text-xs text-muted-foreground">{source.place}</span>
              {/* A page the answer links that the turn's searches and pages didn't find (P5-C9) */}
              {source.found === false && <span className="block text-xs text-destructive">Not among the pages Gen9 found</span>}
            </a>
          </li>
        ))}
      </ol>
    </section>
  );
}

/**
 * Where an answer came from, whatever the model wrote (docs/design/screens/chat.md, "Sources"): a
 * button under the answer, opening the cited pages, then those consulted. A sheet from the right
 * on wider screens, from the bottom on phones. No favicons: they would call other sites.
 */
export function Sources({ sources }: { sources: AnswerSources }) {
  const wide = useSyncExternalStore(subscribe, () => window.matchMedia(WIDE).matches, () => true);
  const all = [...sources.cited, ...sources.consulted];
  if (!all.length) return null;
  const sites = [...new Set(all.map((s) => s.site))];
  const more = sites.length - 2;
  return (
    <Sheet>
      <SheetTrigger
        className="mt-3 inline-flex min-h-8 max-w-full items-center gap-1.5 rounded-full border px-3 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-hidden pointer-coarse:min-h-11"
        aria-label={`Sources: ${all.length} ${all.length === 1 ? "page" : "pages"}`}
      >
        <span className="font-medium text-foreground">Sources</span>
        <span className="truncate">
          · {sites.slice(0, 2).join(", ")}
          {more > 0 && ` +${more}`}
        </span>
      </SheetTrigger>
      <SheetContent side={wide ? "right" : "bottom"} className="max-h-[85dvh] gap-0 overflow-y-auto sm:max-h-none">
        <SheetHeader>
          <SheetTitle>Sources</SheetTitle>
          <SheetDescription>
            {all.length} {all.length === 1 ? "page" : "pages"} this answer came from.
          </SheetDescription>
        </SheetHeader>
        <div className="grid min-w-0 gap-5 px-4 pb-6">
          <SourceList title="Cited" sources={sources.cited} />
          <SourceList title="Also consulted" sources={sources.consulted} />
        </div>
      </SheetContent>
    </Sheet>
  );
}
