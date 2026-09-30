import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";

import { RelativeTime } from "@/components/relative-time";
import { SearchForm } from "@/components/search/search-form";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { AgentError, agentJson, type SearchHit, type SearchMode } from "@/lib/agent";
import { requireSession, type Session } from "@/lib/auth/session";
import { byWordsOnly, MODES, parseMode } from "@/lib/search";

export const metadata: Metadata = { title: "Search" };

// Search past chats (docs/design/screens/search.md): the form stays put while the results for a
// new query stream in behind their own Suspense boundary.
export default async function SearchPage({ searchParams }: PageProps<"/search">) {
  const session = await requireSession("/search");
  const params = await searchParams;
  const q = String(params.q ?? "")
    .trim()
    .slice(0, 500);
  const mode = parseMode(params.mode);

  return (
    <main className="mx-auto w-full max-w-3xl px-5 pt-8 pb-16 sm:px-8 lg:pt-14">
      <h1 className="text-headline font-semibold">Search</h1>
      <p className="mt-1 mb-6 text-muted-foreground">
        Your past chats, by their words, their meaning, or a title you half remember.
      </p>
      <SearchForm q={q} mode={mode} />
      <div className="mt-6">
        {q ? (
          <Suspense key={`${mode}:${q}`} fallback={<Searching />}>
            {mode === "hybrid" ? <All session={session} q={q} /> : <Results session={session} q={q} mode={mode} />}
          </Suspense>
        ) : (
          <p className="text-sm text-muted-foreground">Try a phrase you remember, or what the chat was about.</p>
        )}
      </div>
    </main>
  );
}

// "All": the matches by words at once (tens of milliseconds), then more by meaning under them,
// chats not listed yet, once the router has embedded the query (0.7 to 13 s measured, P3-D9).
// Appended, not re-ranked: nothing moves under the cursor or focus while it arrives
async function All({ session, q }: { session: Session; q: string }) {
  const label = `Results for “${q}”, by All`;
  const byWords = await agentJson<SearchHit[]>(session, `/v1/search?${new URLSearchParams({ q, mode: "keyword", limit: "30" })}`);
  return (
    <section aria-label={label}>
      <p role="status" className="mb-3 text-sm text-muted-foreground">
        {byWords.length
          ? `${byWords.length} ${byWords.length === 1 ? "chat matches" : "chats match"} its words`
          : "No chats match its words"}
      </p>
      {byWords.length > 0 && <Hits hits={byWords} />}
      <Suspense fallback={<LookingByMeaning />}>
        <MoreByMeaning session={session} q={q} listed={byWords.map((hit) => hit.thread_id)} />
      </Suspense>
    </section>
  );
}

async function MoreByMeaning({ session, q, listed }: { session: Session; q: string; listed: string[] }) {
  let hits: SearchHit[];
  try {
    hits = await agentJson<SearchHit[]>(session, `/v1/search?${new URLSearchParams({ q, mode: "semantic", limit: "30" })}`);
  } catch (error) {
    // 503: search by meaning is down; 429: over the usage limit. The matches by words stand
    if (error instanceof AgentError && (error.status === 503 || error.status === 429)) {
      return (
        <p role="status" className="mt-4 text-sm">
          {error.status === 503 ? "Search by meaning is unavailable right now." : "You've reached your model usage limit for now."}
          {" Showing matches by words."}
        </p>
      );
    }
    throw error;
  }
  const more = hits.filter((hit) => !listed.includes(hit.thread_id));
  return (
    <div className="mt-6">
      <h2 className="mb-1 text-sm font-medium">More by meaning</h2>
      <p role="status" className="mb-3 text-sm text-muted-foreground">
        {more.length
          ? `${more.length} more ${more.length === 1 ? "chat" : "chats"} about it`
          : listed.length
            ? "No other chats about it."
            : `No chats match “${q}”.`}
      </p>
      {more.length > 0 && <Hits hits={more} />}
    </div>
  );
}

function LookingByMeaning() {
  return (
    <p role="status" aria-busy="true" className="mt-6 flex items-center gap-2 text-sm text-muted-foreground">
      <Spinner className="size-3.5" /> Looking for chats about it by meaning…
    </p>
  );
}

// Every outcome is a section named for its query and mode, so a screen reader (and a test) can
// tell these results from the ones they replace
async function Results({ session, q, mode }: { session: Session; q: string; mode: SearchMode }) {
  const label = `Results for “${q}”, by ${MODES.find((m) => m.value === mode)?.label}`;
  let hits: SearchHit[];
  try {
    hits = await agentJson<SearchHit[]>(session, `/v1/search?${new URLSearchParams({ q, mode, limit: "30" })}`);
  } catch (error) {
    // 503: search by meaning is down; 429: over the usage limit. Both leave "Words" working
    if (error instanceof AgentError && (error.status === 503 || error.status === 429)) {
      const message =
        error.status === 503 ? "Search by meaning is unavailable right now." : "You've reached your model usage limit for now.";
      return (
        <section aria-label={label}>
          <p role="status" className="text-sm">
            {message} Try Words.
          </p>
        </section>
      );
    }
    throw error;
  }

  // One hit per chat, with its best match (gen9-agent's search)
  const chats = hits;
  if (chats.length === 0) {
    return (
      <section aria-label={label}>
        <p role="status" className="text-sm">
          No chats match “{q}”.{mode !== "hybrid" && " Try All."}
        </p>
      </section>
    );
  }
  return (
    <section aria-label={label}>
      <p role="status" className="mb-3 text-sm text-muted-foreground">
        {chats.length} {chats.length === 1 ? "chat" : "chats"}
        {byWordsOnly(mode, hits) && ". Showing matches by words: search by meaning is unavailable right now."}
      </p>
      <Hits hits={chats} />
    </section>
  );
}

function Hits({ hits }: { hits: SearchHit[] }) {
  return (
    <ul className="divide-y overflow-hidden rounded-[20px] border bg-card">
      {hits.map((hit) => (
        <li key={hit.thread_id}>
          <Link
            href={`/chat/${hit.thread_id}`}
            className="block px-5 py-4 transition-colors outline-hidden hover:bg-muted/60 focus-visible:bg-muted/60 focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:ring-inset"
          >
            <span dir="auto" className="block truncate font-medium">{hit.title}</span>
            {hit.snippet && <span dir="auto" className="mt-1 line-clamp-2 text-sm text-muted-foreground">{hit.snippet}</span>}
            <span className="mt-1.5 block text-xs text-muted-foreground">
              <RelativeTime ms={Date.parse(hit.created_at)} />
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}

function Searching() {
  return (
    <div aria-busy="true" aria-label="Searching" className="divide-y overflow-hidden rounded-[20px] border bg-card">
      {[0, 1, 2].map((i) => (
        <div key={i} className="space-y-2 px-5 py-4">
          <Skeleton className="h-4 w-2/3" />
          <Skeleton className="h-3 w-full" />
          <Skeleton className="h-3 w-1/4" />
        </div>
      ))}
    </div>
  );
}
